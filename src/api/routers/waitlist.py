"""Waitlist API router — public endpoint for invite-only access requests."""
import re
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from ...storage.database import DatabaseManager
from ...utils.logger import get_logger
from ..auth import verify_api_key
from ..limiter import limiter

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/waitlist", tags=["Waitlist"])

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def get_db() -> DatabaseManager:
    return DatabaseManager()


class WaitlistEntry(BaseModel):
    email: str
    name: Optional[str] = Field(None, max_length=100)
    role: Optional[str] = None

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        v = v.strip().lower()
        if not EMAIL_RE.match(v):
            raise ValueError("Invalid email address")
        if len(v) > 255:
            raise ValueError("Email too long")
        return v

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        allowed = {"analyst", "security", "finance", "journalist", "other"}
        if v not in allowed:
            return "other"
        return v


@router.post("")
@limiter.limit("5/minute")
async def join_waitlist(request: Request, entry: WaitlistEntry):
    """
    Add an email to the waitlist.

    Public endpoint — no API key required.
    Rate limited: 5 requests per minute per IP.
    Returns 409 if the email is already registered.
    """
    db = get_db()
    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                # Check for duplicate
                cur.execute(
                    "SELECT id FROM waitlist_entries WHERE email = %s",
                    (entry.email,),
                )
                if cur.fetchone():
                    raise HTTPException(
                        status_code=409,
                        detail="Email already registered on the waitlist.",
                    )

                cur.execute(
                    """
                    INSERT INTO waitlist_entries (email, name, role, created_at)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (entry.email, entry.name, entry.role, datetime.utcnow()),
                )
                conn.commit()

        logger.info(f"Waitlist: new entry {entry.email} (role={entry.role})")
        return {"status": "ok", "message": "You've been added to the waitlist."}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Waitlist insert error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        db.close()


@router.get("")
async def list_waitlist(api_key: str = Depends(verify_api_key)):
    """
    Admin endpoint — returns full waitlist for export.
    Requires X-API-Key authentication.
    """
    db = get_db()
    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, email, name, role, created_at, access_code_sent, notes
                    FROM waitlist_entries
                    ORDER BY created_at DESC
                    """
                )
                rows = cur.fetchall()
                entries = [
                    {
                        "id": r[0],
                        "email": r[1],
                        "name": r[2],
                        "role": r[3],
                        "created_at": r[4].isoformat() if r[4] else None,
                        "access_code_sent": r[5],
                        "notes": r[6],
                    }
                    for r in rows
                ]
        return {"total": len(entries), "entries": entries}

    except Exception as e:
        logger.error(f"Waitlist list error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        db.close()
