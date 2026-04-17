"""
Security middleware — DDoS protection, input sanitization, security headers.

Layer order (outermost → innermost):
  Nginx (rate zones, conn limit, body size, TLS) →
  SecurityMiddleware (null bytes, body size backup, headers, suspicious UAs) →
  slowapi (per-endpoint rate limits) →
  Pydantic (schema validation)
"""
import logging
import re

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, JSONResponse

logger = logging.getLogger(__name__)

# Max body size at app level (backup to Nginx's client_max_body_size)
_MAX_BODY_BYTES = 64 * 1024  # 64 KB

# Paths exempt from body size limit (none currently — all large payloads go through nginx)
_BODY_SIZE_EXEMPT: tuple[str, ...] = ()

# Known scanner / exploit User-Agent substrings (lowercase match)
_BLOCKED_UA_PATTERNS = (
    "sqlmap", "nikto", "nmap", "masscan", "zgrab", "nuclei",
    "dirbuster", "gobuster", "wfuzz", "hydra", "metasploit",
    "python-requests/2.2",  # very old version used in many scanners
)

# Regex: null bytes, CRLF injection, path traversal sequences
_SUSPICIOUS_PATH_RE = re.compile(r"[\x00\r\n]|\.\.[\\/]")


class SecurityMiddleware(BaseHTTPMiddleware):
    """
    Lightweight security layer applied before route handlers.

    Blocks:
    - Null bytes / CRLF injection / path traversal in URL
    - Bodies exceeding _MAX_BODY_BYTES (backup layer after Nginx)
    - Known vulnerability scanner User-Agents

    Adds security headers for direct-to-backend connections
    (Nginx already sets them for proxied traffic, but backend may be
    accessed directly in dev or via internal tooling).
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # --- 1. Path sanity check ---
        raw_path = request.url.path + "?" + request.url.query if request.url.query else request.url.path
        if _SUSPICIOUS_PATH_RE.search(raw_path):
            logger.warning("Blocked suspicious path from %s: %s", _get_ip(request), raw_path[:200])
            return JSONResponse(status_code=400, content={"detail": "Bad request"})

        # --- 2. User-Agent block ---
        ua = (request.headers.get("user-agent") or "").lower()
        if any(pattern in ua for pattern in _BLOCKED_UA_PATTERNS):
            logger.warning("Blocked scanner UA from %s: %s", _get_ip(request), ua[:100])
            return JSONResponse(status_code=403, content={"detail": "Forbidden"})

        # --- 3. Body size check (only for methods that carry a body) ---
        if request.method in ("POST", "PUT", "PATCH") and request.url.path not in _BODY_SIZE_EXEMPT:
            content_length = request.headers.get("content-length")
            if content_length and int(content_length) > _MAX_BODY_BYTES:
                logger.warning(
                    "Blocked oversized body (%s bytes) from %s on %s",
                    content_length, _get_ip(request), request.url.path,
                )
                return JSONResponse(status_code=413, content={"detail": "Request body too large"})

        response: Response = await call_next(request)

        # --- 4. Security headers (backup for direct backend access) ---
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )

        return response


def _get_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
