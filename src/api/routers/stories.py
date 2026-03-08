"""Stories & Graph API router."""
import json
import logging
import time
from collections import Counter
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

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/stories", tags=["Stories"])

# ---------------------------------------------------------------------------
# In-memory cache for the global graph endpoint (invalidated by TTL).
# The graph changes at most once per day (after the narrative pipeline).
# ---------------------------------------------------------------------------
_graph_cache: dict = {}


def _get_cached_graph(min_weight: float, min_momentum: float) -> Optional[dict]:
    entry = _graph_cache.get((min_weight, min_momentum))
    if entry and time.time() < entry["expires_at"]:
        return entry["data"]
    return None


def _set_cached_graph(data: dict, min_weight: float, min_momentum: float, ttl: int = 3600) -> None:
    _graph_cache[(min_weight, min_momentum)] = {"data": data, "expires_at": time.time() + ttl}


def get_db() -> DatabaseManager:
    """Get database connection."""
    return DatabaseManager()


@router.get("/graph")
async def get_graph_network(
    min_edge_weight: float = Query(0.40, description="Min TF-IDF weighted Jaccard for global view (default: 0.40)"),
    min_momentum: float = Query(0.0, description="Exclude nodes below this momentum score (default: 0.0)"),
    api_key: str = Depends(verify_api_key),
):
    """
    Get the full narrative graph: active storyline nodes + edges.

    Returns data structured for react-force-graph (nodes + links).
    The min_edge_weight parameter filters weak edges — use a lower value (e.g. 0.10)
    for denser graphs, higher (e.g. 0.50) for cleaner but sparser views.
    Response is cached for 1 hour per min_edge_weight value.
    """
    cached = _get_cached_graph(min_edge_weight, min_momentum)
    if cached:
        return cached

    db = get_db()
    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                # Nodes: active storylines from the view
                # community_id is included after migration 015 (NULL before)
                cur.execute("""
                    SELECT id, title, summary, narrative_status,
                           category, article_count, momentum_score,
                           key_entities, start_date, last_update,
                           days_active, community_id
                    FROM v_active_storylines
                """)
                node_rows = cur.fetchall()

                # Edges: filtered by weight threshold
                cur.execute("""
                    SELECT source_story_id, target_story_id,
                           weight, relation_type
                    FROM v_storyline_graph
                    WHERE weight >= %s
                """, (min_edge_weight,))
                edge_rows = cur.fetchall()

        nodes = []
        momentum_sum = 0.0
        for r in node_rows:
            entities = r[7] or []
            if isinstance(entities, str):
                try:
                    entities = json.loads(entities)
                except (json.JSONDecodeError, TypeError):
                    entities = []

            node = StorylineNode(
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
                community_id=r[11] if len(r) > 11 else None,
            )
            nodes.append(node)
            momentum_sum += node.momentum_score

        links = [
            StorylineEdge(
                source=r[0],
                target=r[1],
                weight=round(r[2] or 0.0, 3),
                relation_type=r[3] or "relates_to",
            )
            for r in edge_rows
        ]

        # Keep only nodes that appear in at least one edge — isolated nodes
        # (no edge meeting min_edge_weight) just add visual clutter.
        connected_ids = set()
        for link in links:
            connected_ids.add(link.source)
            connected_ids.add(link.target)
        nodes = [n for n in nodes if n.id in connected_ids]

        # Optional momentum filter
        if min_momentum > 0:
            nodes = [n for n in nodes if n.momentum_score >= min_momentum]

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
        _set_cached_graph(response, min_edge_weight, min_momentum)
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
                           ARRAY_AGG(key_entities ORDER BY momentum_score DESC) AS all_entities
                    FROM storylines
                    WHERE narrative_status IN ('emerging', 'active')
                      AND community_id IS NOT NULL
                    GROUP BY community_id
                    ORDER BY COUNT(*) DESC
                """)
                rows = cur.fetchall()

        communities = []
        for r in rows:
            cid, size, avg_mom, sids, titles, all_ents = r

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
            label = top_entities[0].title() if top_entities else f"Community {cid}"

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

                # Recent articles (last 10)
                cur.execute("""
                    SELECT a.id, a.title, a.source, a.published_date
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
