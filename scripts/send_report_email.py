#!/usr/bin/env python3
"""
Send daily intelligence report(s) via email.

Fetches today's reports from the database, converts markdown → HTML → PDF
(weasyprint), and sends a single HTML email with PDF attachments to all
configured recipients via Brevo SMTP relay.

Usage:
    python scripts/send_report_email.py
    python scripts/send_report_email.py --dry-run       # print, do not send
    python scripts/send_report_email.py --date 2026-05-17
"""

import sys
import os
import argparse
import smtplib
import logging
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env", override=False)

from src.utils.logger import get_logger
from src.storage.database import DatabaseManager

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def load_recipients(config_path: str = "config/report_recipients.yaml") -> list[dict]:
    """Return recipients list from YAML config."""
    import yaml
    path = PROJECT_ROOT / config_path
    if not path.exists():
        logger.error(f"Recipients config not found: {path}")
        return []
    with open(path) as f:
        data = yaml.safe_load(f)
    recipients = data.get("recipients") or []
    # filter out any None/comment-only entries
    return [r for r in recipients if r and r.get("email")]


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

DEFAULT_REPORT_TYPES = ("daily", "romania-daily")
WEEKLY_REPORT_TYPES = ("weekly", "recap")


def fetch_today_reports(
    db: DatabaseManager, target_date: date, report_types: tuple[str, ...]
) -> list[dict]:
    """Fetch reports of the given types saved for target_date."""
    placeholders = ",".join(["%s"] * len(report_types))
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT id, report_type, final_content, report_date, slug
                FROM reports
                WHERE report_date = %s
                  AND report_type IN ({placeholders})
                  AND final_content IS NOT NULL
                  AND final_content != ''
                ORDER BY report_type
                """,
                (target_date, *report_types),
            )
            rows = cur.fetchall()

    return [
        {
            "id": row[0],
            "report_type": row[1],
            "content": row[2],
            "report_date": row[3],
            "slug": row[4],
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def markdown_to_html(md_text: str) -> str:
    """Convert markdown string to HTML fragment."""
    import markdown as md_lib
    return md_lib.markdown(
        md_text,
        extensions=["tables", "fenced_code", "nl2br", "sane_lists"],
    )


def render_pdf(html_body: str, report: dict) -> Optional[bytes]:
    """
    Render an HTML fragment to a full PDF (A4) via weasyprint.
    Returns None if weasyprint is not installed or rendering fails.
    """
    try:
        import weasyprint
        from jinja2 import Environment, FileSystemLoader

        env = Environment(loader=FileSystemLoader(str(PROJECT_ROOT / "templates")))
        template = env.get_template("report_pdf.html")
        report_date_str = report["report_date"].strftime("%-d %B %Y")
        full_html = template.render(
            content=html_body,
            report_type=report["report_type"],
            report_date=report_date_str,
        )
        return weasyprint.HTML(string=full_html, base_url=str(PROJECT_ROOT)).write_pdf()
    except ImportError:
        logger.warning("weasyprint not installed — PDF attachment skipped")
        return None
    except Exception as exc:
        logger.warning(f"PDF generation failed for {report['report_type']}: {exc}")
        return None


def build_email_html(
    reports: list[dict],
    global_url: str,
    romania_url: str,
    date_slug: str,
    date_display: str,
    is_weekly: bool = False,
) -> str:
    """Render the HTML email body from the appropriate Jinja2 template."""
    from jinja2 import Environment, FileSystemLoader

    env = Environment(loader=FileSystemLoader(str(PROJECT_ROOT / "templates")))

    def _build_url(base: str, slug: Optional[str]) -> str:
        return f"{base.rstrip('/')}/{slug}" if slug else base

    if is_weekly:
        template = env.get_template("email_weekly.html")
        weekly_report = next((r for r in reports if r["report_type"] == "weekly"), None)
        recap_report = next((r for r in reports if r["report_type"] == "recap"), None)
        return template.render(
            date_str=date_display,
            weekly_report=weekly_report,
            recap_report=recap_report,
            global_url=_build_url(global_url, weekly_report["slug"] if weekly_report else None),
            weekly_pdf_filename=f"intelligence_report_weekly_{date_slug}.pdf",
            recap_pdf_filename=f"intelligence_report_recap_{date_slug}.pdf",
        )

    template = env.get_template("email_report.html")
    global_report = next((r for r in reports if r["report_type"] == "daily"), None)
    romania_report = next((r for r in reports if r["report_type"] == "romania-daily"), None)
    return template.render(
        date_str=date_display,
        global_report=global_report,
        romania_report=romania_report,
        global_url=_build_url(global_url, global_report["slug"] if global_report else None),
        romania_url=_build_url(romania_url, romania_report["slug"] if romania_report else None),
        global_pdf_filename=f"intelligence_report_global_{date_slug}.pdf",
        romania_pdf_filename=f"intelligence_report_romania_{date_slug}.pdf",
    )


# ---------------------------------------------------------------------------
# Email sending
# ---------------------------------------------------------------------------

def send_email(
    recipients: list[dict],
    html_body: str,
    pdf_attachments: list[tuple[str, bytes]],
    subject: str,
    dry_run: bool = False,
) -> bool:
    """
    Send one HTML email with optional PDF attachments to all recipients
    via Brevo SMTP relay (or any SMTP host configured via env vars).
    """
    smtp_host = os.getenv("BREVO_SMTP_HOST", "smtp-relay.brevo.com")
    smtp_port = int(os.getenv("BREVO_SMTP_PORT", "587"))
    smtp_user = os.getenv("BREVO_SMTP_USER", "")
    smtp_pass = os.getenv("BREVO_SMTP_PASS", "")
    from_email = os.getenv("BREVO_FROM_EMAIL", "report@intelligence-ita.com")
    from_name = os.getenv("BREVO_FROM_NAME", "Intelligence ITA")

    if not smtp_user or not smtp_pass:
        logger.error("BREVO_SMTP_USER / BREVO_SMTP_PASS not set — email not sent")
        return False

    to_addresses = [r["email"] for r in recipients]
    if not to_addresses:
        logger.warning("No recipients configured — email not sent")
        return False

    if dry_run:
        logger.info(f"[DRY-RUN] Subject  : {subject}")
        logger.info(f"[DRY-RUN] To       : {to_addresses}")
        logger.info(f"[DRY-RUN] Attachments: {[fname for fname, _ in pdf_attachments]}")
        logger.info("[DRY-RUN] HTML body preview (first 500 chars):")
        logger.info(html_body[:500])
        return True

    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{from_email}>"
    msg["To"] = ", ".join(to_addresses)
    msg["Reply-To"] = from_email

    msg.attach(MIMEText(html_body, "html", "utf-8"))

    for filename, pdf_bytes in pdf_attachments:
        part = MIMEBase("application", "pdf")
        part.set_payload(pdf_bytes)
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", "attachment", filename=filename)
        msg.attach(part)

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as s:
            s.ehlo()
            s.starttls()
            s.ehlo()
            s.login(smtp_user, smtp_pass)
            s.sendmail(from_email, to_addresses, msg.as_string())
        logger.info(f"Email sent successfully to {len(to_addresses)} recipient(s)")
        return True
    except Exception as exc:
        logger.error(f"SMTP send failed: {exc}")
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

_PDF_FILENAME_MAP = {
    "daily": "global",
    "romania-daily": "romania",
    "weekly": "weekly",
    "recap": "recap",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Send intelligence report email")
    parser.add_argument("--dry-run", action="store_true", help="Render without sending")
    parser.add_argument("--date", metavar="YYYY-MM-DD", help="Target date (default: today)")
    parser.add_argument(
        "--report-types",
        metavar="TYPES",
        default=",".join(DEFAULT_REPORT_TYPES),
        help="Comma-separated report types to include (default: daily,romania-daily)",
    )
    args = parser.parse_args()

    target_date = date.fromisoformat(args.date) if args.date else date.today()
    date_slug = target_date.strftime("%Y%m%d")
    date_display = target_date.strftime("%-d %B %Y")
    report_types = tuple(t.strip() for t in args.report_types.split(",") if t.strip())
    is_weekly = any(t in WEEKLY_REPORT_TYPES for t in report_types)

    recipients = load_recipients()
    if not recipients:
        logger.error("No recipients in config/report_recipients.yaml — aborting")
        return 1

    db = DatabaseManager()
    reports = fetch_today_reports(db, target_date, report_types)
    if not reports:
        logger.error(f"No reports of type {report_types} found in DB for {target_date}")
        return 1

    logger.info(f"Reports found: {[r['report_type'] for r in reports]}")

    global_url = os.getenv("REPORT_GLOBAL_URL", "")
    romania_url = os.getenv("REPORT_ROMANIA_URL", "")

    html_body = build_email_html(
        reports, global_url, romania_url, date_slug, date_display, is_weekly=is_weekly
    )

    pdf_attachments: list[tuple[str, bytes]] = []
    for report in reports:
        html_fragment = markdown_to_html(report["content"])
        pdf_bytes = render_pdf(html_fragment, report)
        if pdf_bytes:
            rtype = _PDF_FILENAME_MAP.get(report["report_type"], report["report_type"])
            pdf_attachments.append((f"intelligence_report_{rtype}_{date_slug}.pdf", pdf_bytes))

    if not pdf_attachments:
        logger.warning("No PDFs generated — sending email without attachments")

    if is_weekly:
        subject = f"Report Settimanale Intelligence ITA — {date_display}"
    else:
        subject = f"Report Intelligence ITA — {date_display}"

    success = send_email(
        recipients, html_body, pdf_attachments, subject, dry_run=args.dry_run
    )
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
