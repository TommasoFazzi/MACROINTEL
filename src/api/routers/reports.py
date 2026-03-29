"""Reports API router."""
import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from datetime import datetime, date
from typing import Optional

from ..schemas.common import APIResponse, PaginationMeta
from ..schemas.reports import (
    ReportListItem, ReportDetail, ReportFilters,
    ReportContent, ReportSource, ReportFeedback,
    ReportMetadata
)
from ...storage.database import DatabaseManager
from ..auth import verify_api_key
from ...services.report_compare_service import compare_reports

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/reports", tags=["Reports"])


def _safe_str(value) -> str:
    """Return a valid UTF-8 string, replacing any invalid byte sequences."""
    if value is None:
        return ""
    try:
        if isinstance(value, bytes):
            return value.decode('utf-8', errors='replace')
        return value.encode('utf-8', errors='replace').decode('utf-8', errors='replace')
    except Exception:
        return ""


def _extract_bluf(content: str | None) -> str:
    """Extract first meaningful sentence from markdown report content."""
    if not content:
        return ""
    for line in content.split('\n'):
        line = line.strip()
        if not line or line.startswith('#') or line.startswith('---') or line.startswith('*'):
            continue
        line = line.replace('**', '').replace('*', '').replace('_', '')
        if len(line) > 20:
            return line[:150]
    return ""


def get_db() -> DatabaseManager:
    """Get database connection."""
    return DatabaseManager()


@router.get("")
async def list_reports(
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    status: Optional[str] = Query(None, description="Filter by status (draft, reviewed, approved)"),
    report_type: Optional[str] = Query(None, description="Filter by type (daily, weekly)"),
    date_from: Optional[date] = Query(None, description="Filter reports from this date"),
    date_to: Optional[date] = Query(None, description="Filter reports until this date"),
    api_key: str = Depends(verify_api_key),
):
    """
    List reports with pagination and filters.

    - **page**: Page number (default: 1)
    - **per_page**: Items per page (default: 20, max: 100)
    - **status**: Filter by status (draft, reviewed, approved)
    - **report_type**: Filter by type (daily, weekly)
    - **date_from**: Filter reports from this date
    - **date_to**: Filter reports until this date
    """
    db = get_db()
    try:
        # Build query
        conditions = []
        params = []

        if status:
            conditions.append("status = %s")
            params.append(status)
        if report_type:
            conditions.append("report_type = %s")
            params.append(report_type)
        if date_from:
            conditions.append("report_date >= %s")
            params.append(date_from)
        if date_to:
            conditions.append("report_date <= %s")
            params.append(date_to)

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        with db.get_connection() as conn:
            with conn.cursor() as cur:
                # Get total count
                cur.execute(f"SELECT COUNT(*) FROM reports WHERE {where_clause}", params)
                total = cur.fetchone()[0]

                # Get paginated results
                offset = (page - 1) * per_page
                cur.execute(f"""
                    SELECT
                        id, report_date, report_type, status,
                        metadata->>'title' as title,
                        COALESCE(
                            metadata->>'category',
                            UPPER(metadata->'focus_areas'->>0)
                        ) as category,
                        COALESCE(
                            (metadata->>'article_count')::int,
                            (metadata->>'recent_articles_count')::int,
                            0
                        ) as article_count,
                        generated_at,
                        human_reviewed_at,
                        human_reviewer,
                        LEFT(COALESCE(final_content, draft_content), 400) as content_preview
                    FROM reports
                    WHERE {where_clause}
                    ORDER BY report_date DESC, generated_at DESC
                    LIMIT %s OFFSET %s
                """, params + [per_page, offset])

                reports = [
                    ReportListItem(
                        id=r[0],
                        report_date=r[1],
                        report_type=r[2] or "daily",
                        status=r[3] or "draft",
                        title=r[4] or f"Report {r[1]}",
                        category=r[5],
                        executive_summary=_extract_bluf(_safe_str(r[10])),
                        article_count=r[6] or 0,
                        generated_at=r[7],
                        reviewed_at=r[8],
                        reviewer=r[9]
                    )
                    for r in cur.fetchall()
                ]

        return {
            "success": True,
            "data": {
                "reports": [r.model_dump() for r in reports],
                "pagination": PaginationMeta.calculate(total, page, per_page).model_dump(),
                "filters_applied": ReportFilters(
                    status=status,
                    report_type=report_type,
                    date_from=date_from,
                    date_to=date_to
                ).model_dump()
            },
            "generated_at": datetime.utcnow().isoformat()
        }

    except Exception as e:
        logger.error("List reports error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/compare")
async def compare_two_reports(
    ids: str = Query(..., description="Comma-separated report IDs, e.g. '42,38'"),
    api_key: str = Depends(verify_api_key),
):
    """
    Compare two reports and get LLM-synthesized delta analysis.

    Identifies new developments, resolved topics, trend shifts, and persistent themes.

    - **ids**: Comma-separated report IDs (exactly 2 required)
    """
    db = get_db()
    try:
        # Parse IDs
        try:
            id_parts = [int(x.strip()) for x in ids.split(",") if x.strip()]
        except ValueError:
            raise HTTPException(status_code=422, detail="ids must be comma-separated integers")

        if len(id_parts) != 2:
            raise HTTPException(status_code=422, detail="Exactly 2 report IDs required")

        id_a, id_b = id_parts[0], id_parts[1]

        # Fetch both reports from DB
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                # Fetch report A
                cur.execute("""
                    SELECT id, report_date, report_type, status,
                           draft_content, final_content
                    FROM reports
                    WHERE id = %s
                """, [id_a])
                row_a = cur.fetchone()

                # Fetch report B
                cur.execute("""
                    SELECT id, report_date, report_type, status,
                           draft_content, final_content
                    FROM reports
                    WHERE id = %s
                """, [id_b])
                row_b = cur.fetchone()

        if not row_a or not row_b:
            raise HTTPException(status_code=404, detail="One or both reports not found")

        # Build report dicts for comparison service
        report_a = {
            'id': row_a[0],
            'report_date': row_a[1],
            'report_type': row_a[2],
            'status': row_a[3],
            'draft_content': row_a[4],
            'final_content': row_a[5],
        }
        report_b = {
            'id': row_b[0],
            'report_date': row_b[1],
            'report_type': row_b[2],
            'status': row_b[3],
            'draft_content': row_b[4],
            'final_content': row_b[5],
        }

        # Call comparison service (LLM delta analysis)
        delta = compare_reports(report_a, report_b)

        return {
            "success": True,
            "data": {
                "report_a": {
                    "id": id_a,
                    "date": str(report_a["report_date"]),
                    "type": report_a["report_type"]
                },
                "report_b": {
                    "id": id_b,
                    "date": str(report_b["report_date"]),
                    "type": report_b["report_type"]
                },
                "delta": delta
            },
            "generated_at": datetime.utcnow().isoformat()
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Compare reports error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Comparison failed: {str(e)}")


@router.get("/{report_id}")
async def get_report(report_id: int, api_key: str = Depends(verify_api_key)):
    """
    Get detailed report by ID.

    Returns full report content, sources, and feedback.
    """
    db = get_db()
    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                # Get report
                cur.execute("""
                    SELECT
                        id, report_date, report_type, status,
                        model_used, draft_content, final_content,
                        metadata, sources, generated_at
                    FROM reports
                    WHERE id = %s
                """, [report_id])
                row = cur.fetchone()

                if not row:
                    raise HTTPException(status_code=404, detail="Report not found")

                # Get feedback
                cur.execute("""
                    SELECT section_name, rating, comment
                    FROM report_feedback
                    WHERE report_id = %s
                """, [report_id])
                feedback_rows = cur.fetchall()

                # Get bullet points for articles (will be joined later)
                cur.execute("""
                    SELECT id, ai_analysis->'bullet_points' as bullet_points
                    FROM articles
                    WHERE ai_analysis IS NOT NULL
                """)
                bullets_rows = cur.fetchall()
                bullets_map = {row[0]: row[1] for row in bullets_rows if row[0] and row[1]}

        # Parse content (use _safe_str to handle any invalid UTF-8 sequences in stored data)
        content_text = _safe_str(row[6]) or _safe_str(row[5])
        metadata = row[7] or {}
        sources_data = row[8] or []

        # Build sources list safely
        # Sources can be a flat list or a dict with recent_articles/historical_context
        sources = []
        source_items = []
        if isinstance(sources_data, dict):
            source_items = sources_data.get('recent_articles', [])
            source_items += sources_data.get('historical_context', [])
        elif isinstance(sources_data, list):
            source_items = sources_data

        for s in source_items:
            if isinstance(s, dict):
                article_id = s.get('article_id', 0)
                # Get bullet points from the bullets_map if available
                bullet_points = bullets_map.get(article_id, [])
                # Handle case where bullets are stored as JSON string
                if isinstance(bullet_points, str):
                    import json
                    try:
                        bullet_points = json.loads(bullet_points)
                    except:
                        bullet_points = []

                sources.append(ReportSource(
                    article_id=article_id,
                    title=s.get('title', ''),
                    link=s.get('link', ''),
                    relevance_score=s.get('relevance_score') or s.get('similarity'),
                    bullet_points=bullet_points if isinstance(bullet_points, list) else []
                ))

        # Derive title: metadata title > first line of content > fallback
        title = metadata.get('title')
        if not title and content_text:
            first_line = content_text.strip().split('\n')[0]
            # Strip markdown headers
            title = first_line.lstrip('#').strip()[:120] or f"Report {row[1]}"
        elif not title:
            title = f"Report {row[1]}"

        report = ReportDetail(
            id=row[0],
            report_date=row[1],
            report_type=row[2] or "daily",
            status=row[3] or "draft",
            model_used=row[4],
            content=ReportContent(
                title=title,
                executive_summary=content_text[:500] if content_text else "",
                full_text=content_text,
                sections=[]
            ),
            sources=sources,
            feedback=[
                ReportFeedback(
                    section=f[0] or "general",
                    rating=f[1],
                    comment=f[2]
                )
                for f in feedback_rows
            ],
            metadata=ReportMetadata(
                processing_time_ms=metadata.get('processing_time_ms'),
                token_count=metadata.get('token_count')
            )
        )

        return {
            "success": True,
            "data": report.model_dump(),
            "generated_at": datetime.utcnow().isoformat()
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Get report %s error: %s", report_id, e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")
