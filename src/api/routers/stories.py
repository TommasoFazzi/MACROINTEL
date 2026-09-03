"""Stories & Graph API router."""
import json
import logging
import time
from collections import Counter
from enum import Enum
from fastapi import APIRouter, Depends, HTTPException, Query
from datetime import datetime
from typing import Optional

from ..schemas.common import APIResponse, PaginationMeta
from ..schemas.stories import (
    StorylineNode, StorylineEdge, GraphStats, GraphNetwork,
    StorylineDetail, RelatedStoryline, LinkedArticle, CommunityInfo,
)
from ...storage.database import DatabaseManager
from ..auth import verify_api_key
from ...services.ticker_service import load_tickers_config, get_themes_for_ticker

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/stories", tags=["Stories"])

# ---------------------------------------------------------------------------
# In-memory cache for the global graph endpoint (invalidated by TTL).
# The graph changes at most once per day (after the narrative pipeline).
# ---------------------------------------------------------------------------
_graph_cache: dict = {}


class GraphView(str, Enum):
    """Node projection for the graph endpoint.

    `slim` drops `summary` and `key_entities` — together 27% of the payload and
    read by no showcase consumer. An enum rather than a free-form field list keeps
    the cache key bounded: arbitrary field combinations would let `_graph_cache`
    grow without limit inside a 4 GB container.
    """
    full = "full"
    slim = "slim"


# Response bounds. Edges grow with the square of the node pool, so they are the
# term that actually needs capping: at 1000 nodes the payload is ~0.96 MB, still
# under Next.js's 2 MB data-cache entry limit once base64-encoded.
MAX_NODES_DEFAULT = 1000
MAX_EDGES_DEFAULT = 8000


def _get_cached_graph(cache_key: tuple) -> Optional[dict]:
    entry = _graph_cache.get(cache_key)
    if entry and time.time() < entry["expires_at"]:
        return entry["data"]
    return None


def _set_cached_graph(data: dict, cache_key: tuple, ttl: int = 3600) -> None:
    _graph_cache[cache_key] = {"data": data, "expires_at": time.time() + ttl}


def _parse_bullet_points(bullet_data) -> list[str]:
    """Parse bullet points from JSONB field (can be string or list)."""
    if not bullet_data:
        return []
    if isinstance(bullet_data, list):
        return bullet_data
    if isinstance(bullet_data, str):
        try:
            parsed = json.loads(bullet_data)
            return parsed if isinstance(parsed, list) else []
        except (json.JSONDecodeError, TypeError):
            return []
    return []


def get_db() -> DatabaseManager:
    """Get database connection."""
    return DatabaseManager()


@router.get("/graph")
async def get_graph_network(
    min_edge_weight: float = Query(0.10, description="Min TF-IDF weighted Jaccard for global view (default: 0.10)"),
    min_momentum: float = Query(0.0, description="Exclude nodes below this momentum score (default: 0.0)"),
    view: GraphView = Query(
        GraphView.full,
        description="Projection: 'full' (default, all node fields) or 'slim' "
                    "(drops summary and key_entities — for showcase consumers)",
    ),
    max_nodes: int = Query(
        MAX_NODES_DEFAULT, ge=1, le=MAX_NODES_DEFAULT,
        description=f"Cap on returned nodes, highest momentum first (default/max: {MAX_NODES_DEFAULT})",
    ),
    max_edges: int = Query(
        MAX_EDGES_DEFAULT, ge=1, le=MAX_EDGES_DEFAULT,
        description=f"Cap on returned edges, heaviest first (default/max: {MAX_EDGES_DEFAULT})",
    ),
    api_key: str = Depends(verify_api_key),
):
    """
    Get the narrative graph: active storyline nodes + edges.

    Returns data structured for react-force-graph (nodes + links).
    The min_edge_weight parameter filters weak edges — use a lower value (e.g. 0.05)
    for denser graphs, higher (e.g. 0.30) for cleaner but sparser views.
    Default 0.10 is calibrated for TF-IDF weighted Jaccard (which compresses scores
    compared to plain Jaccard: common entities like 'USA' contribute very little).

    Bounded by construction (max_nodes / max_edges). Edge count grows with the
    SQUARE of the node pool, so an unbounded response degrades super-linearly as
    the corpus grows: in Aug 2026 it reached 2402 nodes / 41344 edges / 5.82 MB,
    which exceeded the landing page's fetch budget and Next.js's 2 MB data-cache
    entry limit, silently falling back to static content. Nodes are selected first
    (highest momentum), then only edges *between selected nodes* are read, so
    `links` can never reference an id absent from `nodes`.

    Response is cached for 1 hour per parameter combination.
    """
    cache_key = (min_edge_weight, min_momentum, view.value, max_nodes, max_edges)
    cached = _get_cached_graph(cache_key)
    if cached:
        return cached

    db = get_db()
    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                # Nodes: active storylines — read directly from storylines table
                # to include community_name (added migration 022, not in v_active_storylines view).
                # summary/key_entities are the two heavy fields (27% of the payload
                # combined) and are never read by showcase consumers, so 'slim'
                # selects NULL in their place rather than transferring them.
                # Interpolated, not parameterised: these are column names, which
                # cannot be bound. Safe only because `view` is a closed enum
                # validated by FastAPI — never interpolate caller-supplied text.
                heavy_fields = (
                    "summary, key_entities"
                    if view is GraphView.full
                    else "NULL AS summary, NULL AS key_entities"
                )
                cur.execute(f"""
                    SELECT id, title, {heavy_fields}, narrative_status,
                           category, article_count, momentum_score,
                           start_date, last_update,
                           EXTRACT(DAY FROM NOW() - start_date)::INTEGER AS days_active,
                           community_id, community_name
                    FROM storylines
                    WHERE narrative_status IN ('emerging', 'active', 'stabilized')
                      AND momentum_score >= %s
                    ORDER BY momentum_score DESC, last_update DESC
                    LIMIT %s
                """, (min_momentum, max_nodes))
                node_rows = cur.fetchall()

                # Edges: only those with BOTH endpoints among the selected nodes.
                # Filtering in SQL (rather than fetching every edge and pruning in
                # Python) is what bounds the payload: the edge set is the quadratic
                # term. It also removes the dangling links the old code could emit,
                # since `links` was never reconciled against the surviving nodes.
                selected_ids = [r[0] for r in node_rows]
                if selected_ids:
                    cur.execute("""
                        SELECT source_story_id, target_story_id,
                               weight, relation_type
                        FROM v_storyline_graph
                        WHERE weight >= %s
                          AND source_story_id = ANY(%s)
                          AND target_story_id = ANY(%s)
                        ORDER BY weight DESC
                        LIMIT %s
                    """, (min_edge_weight, selected_ids, selected_ids, max_edges))
                    edge_rows = cur.fetchall()
                else:
                    edge_rows = []

        # Column order: id, title, summary, key_entities, narrative_status, category,
        # article_count, momentum_score, start_date, last_update, days_active,
        # community_id, community_name
        nodes = []
        for r in node_rows:
            entities = r[3] or []
            if isinstance(entities, str):
                try:
                    entities = json.loads(entities)
                except (json.JSONDecodeError, TypeError):
                    entities = []

            node = StorylineNode(
                id=r[0],
                title=r[1],
                summary=r[2],
                key_entities=entities if isinstance(entities, list) else [],
                narrative_status=r[4] or "active",
                category=r[5],
                article_count=r[6] or 0,
                momentum_score=round(r[7] or 0.0, 3),
                start_date=r[8].isoformat() if r[8] else None,
                last_update=r[9].isoformat() if r[9] else None,
                days_active=r[10],
                community_id=r[11],
                community_name=r[12],
            )
            nodes.append(node)

        links = [
            StorylineEdge(
                source=r[0],
                target=r[1],
                weight=round(r[2] or 0.0, 3),
                relation_type=r[3] or "relates_to",
            )
            for r in edge_rows
        ]

        # Keep nodes that are either connected OR have high momentum.
        # TF-IDF weighted Jaccard compresses edge scores, so many important
        # storylines (e.g. "Terremoto in Turchia" with 50 articles, momentum 0.9)
        # may have no edges above the threshold but must remain visible as
        # bright "lone stars" on the graph for the analyst.
        connected_ids = set()
        for link in links:
            connected_ids.add(link.source)
            connected_ids.add(link.target)
        HIGH_MOMENTUM_THRESHOLD = 0.4  # keep isolated nodes if momentum >= this
        nodes = [
            n for n in nodes
            if n.id in connected_ids or n.momentum_score >= HIGH_MOMENTUM_THRESHOLD
        ]

        # No link/node reconciliation needed: `connected_ids` comes from `links`
        # itself, so both endpoints of every surviving edge are kept above by
        # construction. Dangling links came from the edge query being unscoped;
        # the ANY(%s) predicates fix that at the source.
        # min_momentum is applied in the node query, so no Python-side pass either.

        avg_momentum = round(
            sum(n.momentum_score for n in nodes) / len(nodes), 3
        ) if nodes else 0.0

        community_ids = set(n.community_id for n in nodes if n.community_id is not None)
        avg_epn = round(len(links) / len(nodes), 1) if nodes else 0.0

        graph = GraphNetwork(
            nodes=nodes,
            links=links,
            stats=GraphStats(
                total_nodes=len(nodes),
                total_edges=len(links),
                avg_momentum=avg_momentum,
                communities_count=len(community_ids),
                avg_edges_per_node=avg_epn,
            ),
        )

        response = {
            "success": True,
            "data": graph.model_dump(),
            "generated_at": datetime.utcnow().isoformat(),
        }
        _set_cached_graph(response, cache_key)
        return response

    except Exception as e:
        logger.error("Graph network error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/communities")
async def list_communities(
    api_key: str = Depends(verify_api_key),
):
    """
    List all detected Louvain communities with their top storylines and key entities.
    Communities are sorted by size (largest first).
    """
    db = get_db()
    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT community_id,
                           COUNT(*) AS size,
                           AVG(momentum_score) AS avg_momentum,
                           ARRAY_AGG(id ORDER BY momentum_score DESC) AS storyline_ids,
                           ARRAY_AGG(title ORDER BY momentum_score DESC) AS titles,
                           ARRAY_AGG(key_entities ORDER BY momentum_score DESC) AS all_entities,
                           MIN(community_name) AS community_name
                    FROM storylines
                    WHERE narrative_status IN ('emerging', 'active')
                      AND community_id IS NOT NULL
                    GROUP BY community_id
                    ORDER BY COUNT(*) DESC
                """)
                rows = cur.fetchall()

        communities = []
        for r in rows:
            cid, size, avg_mom, sids, titles, all_ents, db_community_name = r

            # Aggregate entities across all storylines in community
            entity_counter: Counter = Counter()
            for ent_list in all_ents:
                if isinstance(ent_list, list):
                    entity_counter.update(e.lower() for e in ent_list)
                elif isinstance(ent_list, str):
                    try:
                        parsed = json.loads(ent_list)
                        if isinstance(parsed, list):
                            entity_counter.update(e.lower() for e in parsed)
                    except (json.JSONDecodeError, TypeError):
                        pass
            top_entities = [e for e, _ in entity_counter.most_common(10)]
            # Use LLM-generated name if available, fall back to top entity
            label = db_community_name or (
                top_entities[0].title() if top_entities else f"Community {cid}"
            )

            # Top 5 storylines by momentum (lightweight summary)
            top_storylines = [
                {"id": sids[i], "title": titles[i]}
                for i in range(min(5, len(sids)))
            ]

            communities.append({
                "community_id": cid,
                "size": size,
                "label": label,
                "top_storylines": top_storylines,
                "key_entities": top_entities,
                "avg_momentum": round(avg_mom or 0, 3),
            })

        return {
            "success": True,
            "data": {"communities": communities, "total": len(communities)},
            "generated_at": datetime.utcnow().isoformat(),
        }

    except Exception as e:
        logger.error("Communities error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/tickers")
async def list_tickers(
    api_key: str = Depends(verify_api_key),
):
    """
    List all available market tickers organized by category.

    Returns ticker symbols, names, exchanges, and aliases for use in frontend filters.
    Data is cached for 1 hour (YAML config loaded once).
    """
    try:
        config = load_tickers_config()

        # Organize by category
        categories = {}
        for ticker_info in config['tickers'].values():
            category = ticker_info['category']
            if category not in categories:
                categories[category] = []
            categories[category].append(ticker_info)

        return {
            "success": True,
            "data": {
                "categories": categories,
                "total": config['total']
            },
            "generated_at": datetime.utcnow().isoformat(),
        }

    except Exception as e:
        logger.error("Tickers list error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/ticker/{ticker}")
async def get_ticker_themes(
    ticker: str,
    top_n: int = Query(5, ge=1, le=20, description="Maximum storylines to return"),
    days: int = Query(30, ge=1, le=365, description="Look back this many days"),
    api_key: str = Depends(verify_api_key),
):
    """
    Find storylines correlated to a specific ticker symbol.

    Searches for the ticker's aliases in article key_entities and aggregates
    the associated storylines by article count and momentum score.

    Returns the top N most-relevant storylines from the past N days.
    """
    db = get_db()
    try:
        result = get_themes_for_ticker(db, ticker, days=days, top_n=top_n)

        return {
            "success": True,
            "data": result,
            "generated_at": datetime.utcnow().isoformat(),
        }

    except ValueError as e:
        # Ticker not found
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("Ticker themes error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/{storyline_id}/network")
async def get_ego_network(
    storyline_id: int,
    min_weight: float = Query(0.05, description="Min edge weight for ego network (includes weak signals)"),
    api_key: str = Depends(verify_api_key),
):
    """
    Ego network for a single storyline: returns the center node, all its
    neighbors (both edge directions), and the edges connecting them.

    Use min_weight=0.05 to surface weak signals hidden in the global view.
    """
    db = get_db()
    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                # Center node
                cur.execute("""
                    SELECT id, title, summary, narrative_status,
                           category, article_count, momentum_score,
                           key_entities, start_date, last_update,
                           EXTRACT(DAY FROM NOW() - start_date)::INTEGER AS days_active
                    FROM storylines
                    WHERE id = %s
                """, (storyline_id,))
                center_row = cur.fetchone()
                if not center_row:
                    raise HTTPException(status_code=404, detail="Storyline not found")

                # Neighbors + edge data (both directions)
                cur.execute("""
                    SELECT s.id, s.title, s.summary, s.narrative_status,
                           s.category, s.article_count, s.momentum_score,
                           s.key_entities, s.start_date, s.last_update,
                           EXTRACT(DAY FROM NOW() - s.start_date)::INTEGER AS days_active,
                           e.weight, e.relation_type,
                           e.source_story_id, e.target_story_id
                    FROM storyline_edges e
                    JOIN storylines s ON (
                        CASE WHEN e.source_story_id = %s
                             THEN e.target_story_id
                             ELSE e.source_story_id
                        END = s.id
                    )
                    WHERE (e.source_story_id = %s OR e.target_story_id = %s)
                      AND s.narrative_status IN ('emerging', 'active', 'stabilized')
                      AND e.weight >= %s
                    ORDER BY e.weight DESC
                """, (storyline_id, storyline_id, storyline_id, min_weight))
                neighbor_rows = cur.fetchall()

        def _make_node(r):
            entities = r[7] or []
            if isinstance(entities, str):
                try:
                    entities = json.loads(entities)
                except (json.JSONDecodeError, TypeError):
                    entities = []
            return StorylineNode(
                id=r[0], title=r[1], summary=r[2],
                narrative_status=r[3] or "active",
                category=r[4], article_count=r[5] or 0,
                momentum_score=round(r[6] or 0.0, 3),
                key_entities=entities if isinstance(entities, list) else [],
                start_date=r[8].isoformat() if r[8] else None,
                last_update=r[9].isoformat() if r[9] else None,
                days_active=r[10],
            )

        center_node = _make_node(center_row)
        neighbors = [_make_node(r) for r in neighbor_rows]
        edges = [
            StorylineEdge(
                source=r[13], target=r[14],
                weight=round(r[11] or 0.0, 3),
                relation_type=r[12] or "relates_to",
            )
            for r in neighbor_rows
        ]

        return {
            "success": True,
            "data": {
                "center_node": center_node.model_dump(),
                "neighbors": [n.model_dump() for n in neighbors],
                "edges": [e.model_dump() for e in edges],
            },
            "generated_at": datetime.utcnow().isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Ego network %s error: %s", storyline_id, e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("")
async def list_storylines(
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    status: Optional[str] = Query(
        None,
        description="Filter by narrative_status (emerging, active, stabilized, archived)",
    ),
    api_key: str = Depends(verify_api_key),
):
    """
    List storylines with pagination, ordered by momentum_score DESC.
    """
    db = get_db()
    try:
        conditions = ["1=1"]
        params: list = []

        if status:
            conditions.append("narrative_status = %s")
            params.append(status)
        else:
            # Default: only active storylines
            conditions.append("narrative_status IN ('emerging', 'active')")

        where_clause = " AND ".join(conditions)

        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT COUNT(*) FROM storylines WHERE {where_clause}",
                    params,
                )
                total = cur.fetchone()[0]

                offset = (page - 1) * per_page
                cur.execute(f"""
                    SELECT id, title, summary, narrative_status,
                           category, article_count, momentum_score,
                           key_entities, start_date, last_update,
                           EXTRACT(DAY FROM NOW() - start_date)::INTEGER AS days_active
                    FROM storylines
                    WHERE {where_clause}
                    ORDER BY momentum_score DESC, last_update DESC
                    LIMIT %s OFFSET %s
                """, params + [per_page, offset])

                rows = cur.fetchall()

        storylines = []
        for r in rows:
            entities = r[7] or []
            if isinstance(entities, str):
                try:
                    entities = json.loads(entities)
                except (json.JSONDecodeError, TypeError):
                    entities = []

            storylines.append(StorylineNode(
                id=r[0],
                title=r[1],
                summary=r[2],
                narrative_status=r[3] or "active",
                category=r[4],
                article_count=r[5] or 0,
                momentum_score=round(r[6] or 0.0, 3),
                key_entities=entities if isinstance(entities, list) else [],
                start_date=r[8].isoformat() if r[8] else None,
                last_update=r[9].isoformat() if r[9] else None,
                days_active=r[10],
            ).model_dump())

        return {
            "success": True,
            "data": {
                "storylines": storylines,
                "pagination": PaginationMeta.calculate(total, page, per_page).model_dump(),
            },
            "generated_at": datetime.utcnow().isoformat(),
        }

    except Exception as e:
        logger.error("List storylines error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/{storyline_id}")
async def get_storyline_detail(storyline_id: int, api_key: str = Depends(verify_api_key)):
    """
    Get detailed storyline with related storylines and recent articles.
    """
    db = get_db()
    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                # Storyline base data
                cur.execute("""
                    SELECT id, title, summary, narrative_status,
                           category, article_count, momentum_score,
                           key_entities, start_date, last_update,
                           EXTRACT(DAY FROM NOW() - start_date)::INTEGER AS days_active
                    FROM storylines
                    WHERE id = %s
                """, [storyline_id])
                row = cur.fetchone()

                if not row:
                    raise HTTPException(status_code=404, detail="Storyline not found")

                # Related storylines via edges (both directions)
                cur.execute("""
                    SELECT s.id, s.title, e.weight, e.relation_type
                    FROM storyline_edges e
                    JOIN storylines s ON (
                        CASE WHEN e.source_story_id = %s
                             THEN e.target_story_id
                             ELSE e.source_story_id
                        END = s.id
                    )
                    WHERE (e.source_story_id = %s OR e.target_story_id = %s)
                      AND s.narrative_status IN ('emerging', 'active')
                    ORDER BY e.weight DESC
                    LIMIT 10
                """, [storyline_id, storyline_id, storyline_id])
                related_rows = cur.fetchall()

                # Recent articles (last 10) with bullet points
                cur.execute("""
                    SELECT a.id, a.title, a.source, a.published_date,
                           a.ai_analysis->'bullet_points' as bullet_points
                    FROM article_storylines als
                    JOIN articles a ON als.article_id = a.id
                    WHERE als.storyline_id = %s
                    ORDER BY a.published_date DESC
                    LIMIT 10
                """, [storyline_id])
                article_rows = cur.fetchall()

        entities = row[7] or []
        if isinstance(entities, str):
            try:
                entities = json.loads(entities)
            except (json.JSONDecodeError, TypeError):
                entities = []

        storyline_node = StorylineNode(
            id=row[0],
            title=row[1],
            summary=row[2],
            narrative_status=row[3] or "active",
            category=row[4],
            article_count=row[5] or 0,
            momentum_score=round(row[6] or 0.0, 3),
            key_entities=entities if isinstance(entities, list) else [],
            start_date=row[8].isoformat() if row[8] else None,
            last_update=row[9].isoformat() if row[9] else None,
            days_active=row[10],
        )

        detail = StorylineDetail(
            storyline=storyline_node,
            related_storylines=[
                RelatedStoryline(
                    id=r[0], title=r[1],
                    weight=round(r[2] or 0.0, 3),
                    relation_type=r[3] or "relates_to",
                )
                for r in related_rows
            ],
            recent_articles=[
                LinkedArticle(
                    id=r[0], title=r[1],
                    source=r[2],
                    published_date=r[3].isoformat() if r[3] else None,
                    bullet_points=_parse_bullet_points(r[4]) if len(r) > 4 else []
                )
                for r in article_rows
            ],
        )

        return {
            "success": True,
            "data": detail.model_dump(),
            "generated_at": datetime.utcnow().isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Storyline detail %s error: %s", storyline_id, e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")
