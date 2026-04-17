"""Shared API authentication module."""
import os
import secrets
import logging

from fastapi import HTTPException, Security, Request
from fastapi.security import APIKeyHeader

logger = logging.getLogger(__name__)

API_KEY_NAME = "X-API-Key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

INTELLIGENCE_API_KEY = os.getenv("INTELLIGENCE_API_KEY")
ORACLE_ADMIN_KEY = os.getenv("ORACLE_ADMIN_KEY")
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")

if not INTELLIGENCE_API_KEY:
    if ENVIRONMENT == "production":
        logger.critical(
            "INTELLIGENCE_API_KEY not set in PRODUCTION — all requests will be rejected. "
            "Set the env var. Generate one with: "
            'python -c "import secrets; print(secrets.token_urlsafe(32))"'
        )
    else:
        logger.warning(
            "INTELLIGENCE_API_KEY not set — API authentication is DISABLED (dev mode). "
            "Set the env var for production."
        )


async def verify_api_key(request: Request, api_key: str = Security(api_key_header)) -> str:
    """Verify API key for protected endpoints."""
    client_ip = request.client.host if request.client else "unknown"

    if not INTELLIGENCE_API_KEY:
        if ENVIRONMENT == "production":
            logger.error("Auth rejected (no API key configured) from %s", client_ip)
            raise HTTPException(
                status_code=503,
                detail="Service misconfigured — contact administrator.",
            )
        # Development mode: allow access
        return "dev_mode"

    if not api_key:
        logger.warning("Auth failed (missing key) from %s", client_ip)
        raise HTTPException(
            status_code=401,
            detail="API key required. Provide X-API-Key header.",
        )

    # Accept ORACLE_ADMIN_KEY as a valid alternative (admin bypass for Oracle rate limit)
    if ORACLE_ADMIN_KEY and secrets.compare_digest(api_key, ORACLE_ADMIN_KEY):
        return api_key

    if not secrets.compare_digest(api_key, INTELLIGENCE_API_KEY):
        logger.warning("Auth failed (invalid key) from %s", client_ip)
        raise HTTPException(status_code=403, detail="Invalid API key")

    return api_key
