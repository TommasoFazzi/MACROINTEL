"""
Public Insights API router — no authentication required.
Exposes a subset of intelligence reports as public /insights pages for SEO.

Reports must have is_public=TRUE and a non-null slug to appear here.
Slugs are evergreen (no dates in URL) to accumulate SEO authority over time.
"""
import re
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Request

from ...storage.database import DatabaseManager
from ...utils.logger import get_logger
from ..limiter import limiter

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/insights", tags=["Insights"])


def get_db() -> DatabaseManager:
    return DatabaseManager()


def _extract_executive_summary(content: str) -> str:
    """Extract executive summary section from markdown report text."""
    if not content:
        return ""

    patterns = [
        r"##\s+Executive Summary\s*\n(.*?)(?=\n##|\Z)",
        r"##\s+Sommario Esecutivo\s*\n(.*?)(?=\n##|\Z)",
        r"##\s+Summary\s*\n(.*?)(?=\n##|\Z)",
    ]
    for pattern in patterns:
        match = re.search(pattern, content, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip()

    return content[:800].strip()


def _extract_title(content: str) -> Optional[str]:
    """Extract the first H1 heading from markdown content."""
    if not content:
        return None
    match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
    if match:
        title = re.sub(r"[^\w\s\-:,'\./]", "", match.group(1)).strip()
        return title or None
    return None


def _get_content(draft: Optional[str], final: Optional[str]) -> str:
    """Return final content if reviewed, otherwise draft."""
    return (final or draft or "").strip()


def _preview(text: str, length: int = 160) -> str:
    """Return a clean text preview, stripping markdown."""
    plain = re.sub(r"[#*_`\[\]()>]", "", text)
    plain = re.sub(r"\s+", " ", plain).strip()
    if len(plain) > length:
        return plain[:length].rstrip() + "…"
    return plain


@router.get("")
@limiter.limit("30/minute")
async def list_insights(request: Request, limit: int = 20):
    """
    List public intelligence briefings for the /insights landing page.

    Returns reports with is_public=TRUE, ordered by report_date DESC.
    Maximum 50 results. No authentication required.
    """
    limit = min(limit, 50)
    db = get_db()
    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        id,
                        slug,
                        report_date,
                        report_type,
                        COALESCE(metadata->>'category', report_type) AS category,
                        draft_content,
                        final_content
                    FROM reports
                    WHERE is_public = TRUE AND slug IS NOT NULL
                    ORDER BY report_date DESC, generated_at DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
                rows = cur.fetchall()

        insights = []
        for r in rows:
            rid, slug, report_date, rtype, category, draft, final = r
            content = _get_content(draft, final)
            title = _extract_title(content)
            summary = _extract_executive_summary(content)
            insights.append(
                {
                    "id": rid,
                    "slug": slug,
                    "title": title or f"Intelligence Briefing — {report_date}",
                    "published_at": report_date.isoformat() if report_date else None,
                    "report_type": rtype,
                    "category": category,
                    "summary_preview": _preview(summary, 160),
                }
            )

        return {"total": len(insights), "insights": insights}

    except Exception as e:
        logger.error(f"Insights list error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        db.close()


@router.get("/{slug}")
@limiter.limit("60/minute")
async def get_insight(request: Request, slug: str):
    """
    Get a single public insight by slug.

    Returns the executive summary and first half of the report body.
    is_truncated=true indicates additional content exists in the dashboard.
    No authentication required.
    """
    if not re.match(r"^[a-z0-9\-]{1,500}$", slug):
        raise HTTPException(status_code=404, detail="Insight not found")

    db = get_db()
    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        id,
                        slug,
                        report_date,
                        report_type,
                        COALESCE(metadata->>'category', report_type) AS category,
                        draft_content,
                        final_content
                    FROM reports
                    WHERE is_public = TRUE AND slug = %s
                    LIMIT 1
                    """,
                    (slug,),
                )
                row = cur.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Insight not found")

        rid, slug, report_date, rtype, category, draft, final = row
        content = _get_content(draft, final)
        title = _extract_title(content)
        executive_summary = _extract_executive_summary(content)

        remaining = content[content.find(executive_summary) + len(executive_summary):]
        preview_len = len(remaining) // 2
        content_preview = remaining[:preview_len].strip() if remaining else ""

        return {
            "id": rid,
            "slug": slug,
            "title": title or f"Intelligence Briefing — {report_date}",
            "published_at": report_date.isoformat() if report_date else None,
            "report_type": rtype,
            "category": category,
            "executive_summary": executive_summary,
            "content_preview": content_preview,
            "is_truncated": bool(remaining and preview_len < len(remaining)),
            "summary_preview": _preview(executive_summary, 160),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Insight detail error ({slug}): {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        db.close()
