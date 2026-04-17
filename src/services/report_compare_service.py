"""
Report comparison service using LLM delta analysis.

Compares two reports and generates structured delta identifying new developments,
resolved topics, trend shifts, and persistent themes.
"""

import re
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Dict, List, Any

from ..llm.llm_factory import LLMFactory
from ..utils.logger import get_logger

logger = get_logger(__name__)


def _extract_delta_from_xml(xml_str: str) -> Dict[str, List[str]]:
    """
    Extract delta analysis from XML response.

    Safely extracts <analysis> block with regex before parsing to handle
    LLM models that add extra text before/after the XML.

    Returns dict with keys: new_developments, resolved_topics, trend_shifts, persistent_themes
    """
    # Extract XML block with regex (handles LLM "chatter")
    match = re.search(r'<analysis>.*?</analysis>', xml_str, re.DOTALL)
    if not match:
        raise ValueError("LLM response did not contain <analysis>...</analysis> block")

    xml_block = match.group(0)

    try:
        root = ET.fromstring(xml_block)
    except ET.ParseError as e:
        logger.error(f"XML parse error: {e}\nXML: {xml_block[:500]}")
        raise ValueError(f"Failed to parse LLM XML response: {e}")

    # Extract 4 sections
    def extract_items(tag_name: str) -> List[str]:
        items = []
        section = root.find(tag_name)
        if section is not None:
            for item in section.findall('item'):
                text = item.text
                if text and text.strip():
                    items.append(text.strip())
        return items

    return {
        'new_developments': extract_items('new_developments'),
        'resolved_topics': extract_items('resolved_topics'),
        'trend_shifts': extract_items('trend_shifts'),
        'persistent_themes': extract_items('persistent_themes'),
    }


def compare_reports(report_a: Dict[str, Any], report_b: Dict[str, Any]) -> Dict[str, List[str]]:
    """
    Compare two reports and generate LLM-synthesized delta analysis.

    Args:
        report_a: Report dict from database (id, report_date, report_type, draft_content, final_content)
        report_b: Report dict from database (same structure)

    Returns:
        Dict with keys: new_developments, resolved_topics, trend_shifts, persistent_themes
        Each value is a list of bullet-point strings (3-8 items per section)

    Raises:
        ValueError: If reports cannot be compared or LLM response is malformed
    """
    # Order by report_date: older = "Precedente", newer = "Recente"
    reports_by_date = sorted(
        [(report_a, 'a'), (report_b, 'b')],
        key=lambda x: x[0]['report_date']
    )
    older_report, older_id = reports_by_date[0]
    newer_report, newer_id = reports_by_date[1]

    # Extract content (prefer final_content, fallback to draft_content)
    content_old = older_report.get('final_content') or older_report.get('draft_content') or ''
    content_new = newer_report.get('final_content') or newer_report.get('draft_content') or ''

    # Truncate to 35000 chars each
    content_old = str(content_old)[:35000]
    content_new = str(content_new)[:35000]

    date_old = older_report['report_date']
    date_new = newer_report['report_date']

    logger.info(
        f"Comparing reports: {date_old} (id={older_report['id']}) vs {date_new} (id={newer_report['id']})"
    )

    # Build prompt
    prompt = f"""Sei un analista di intelligence. Confronta il "Report Precedente" con il "Report Recente".

Report Precedente (Data: {date_old}):
{content_old}

Report Recente (Data: {date_new}):
{content_new}

Identifica:
- Nuovi sviluppi (presenti solo nel Report Recente)
- Temi risolti (presenti nel Report Precedente ma assenti/conclusi nel Report Recente)
- Shift di trend (presenti in entrambi ma con dinamica cambiata)
- Temi persistenti (stabili/invariati in entrambi)

Produci SOLO questo XML, senza altro testo:
<analysis>
  <new_developments><item>...</item><item>...</item></new_developments>
  <resolved_topics><item>...</item><item>...</item></resolved_topics>
  <trend_shifts><item>...</item><item>...</item></trend_shifts>
  <persistent_themes><item>...</item><item>...</item></persistent_themes>
</analysis>"""

    try:
        # T1 (Gemini 3.1 Pro) for deep-reasoning report comparison
        model = LLMFactory.get("t1")
        response_text = model.generate(prompt, max_tokens=2048, temperature=0.3)
        logger.info(f"LLM delta analysis generated ({len(response_text)} chars)")

        # Extract and parse XML
        delta = _extract_delta_from_xml(response_text)

        logger.info(
            f"Delta extracted: {len(delta.get('new_developments', []))} new, "
            f"{len(delta.get('resolved_topics', []))} resolved, "
            f"{len(delta.get('trend_shifts', []))} shifts, "
            f"{len(delta.get('persistent_themes', []))} persistent"
        )

        return delta

    except Exception as e:
        logger.error(f"Failed to compare reports: {e}", exc_info=True)
        raise ValueError(f"Report comparison failed: {str(e)}")
