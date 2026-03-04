"""
Oracle auth module — pluggable for Phase 2+ (login, BYOK, billing).

Phase 1: private mode (ORACLE_MODE=private)
  - Whitelist check via ORACLE_ALLOWED_API_KEYS env var (comma-separated)
  - Returns UserContext with tier="admin"

Phase 2+ stubs: set ORACLE_MODE=alpha|freemium|saas and implement below.
"""

import hashlib
import os
from dataclasses import dataclass, field
from typing import Optional

from fastapi import Depends, HTTPException, Request

from .auth import verify_api_key

ORACLE_MODE = os.getenv("ORACLE_MODE", "private")  # private | alpha | freemium | saas

_ALLOWED_KEYS: frozenset = frozenset(
    k.strip() for k in os.getenv("ORACLE_ALLOWED_API_KEYS", "").split(",") if k.strip()
)


@dataclass
class UserContext:
    user_id: str                          # Phase 1: sha256(api_key)[:16]. Phase 2: UUID from DB.
    tier: str                             # Phase 1: "admin". Phase 2: "free"|"premium"|"admin".
    is_admin: bool
    gemini_api_key: Optional[str] = field(default=None)  # BYOK key, never logged.


async def verify_oracle_user(
    request: Request,
    api_key: str = Depends(verify_api_key),
) -> UserContext:
    """
    Single dependency for all oracle endpoints.
    Phase 1 (private): whitelist check → UserContext(admin).
    Phase 2+: swap in JWT / DB lookup without touching oracle.py.
    """
    if ORACLE_MODE == "private":
        if _ALLOWED_KEYS and api_key not in _ALLOWED_KEYS:
            raise HTTPException(status_code=403, detail="Oracle access not authorized")
        return UserContext(
            user_id=hashlib.sha256(api_key.encode()).hexdigest()[:16],
            tier="admin",
            is_admin=True,
        )

    # Phase 2+: implement JWT / DB lookup here.
    raise HTTPException(status_code=501, detail="Auth mode not implemented")
