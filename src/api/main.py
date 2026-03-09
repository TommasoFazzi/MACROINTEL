"""
FastAPI Backend for Intelligence Map

Provides REST API endpoints for entity visualization on the map.
"""
import sys
from pathlib import Path
from typing import Optional

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import os
from fastapi import FastAPI, HTTPException, Depends, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from src.storage.database import DatabaseManager
from src.utils.logger import get_logger
from src.api.routers import dashboard, reports, stories, oracle, map as map_router
from src.api.auth import verify_api_key
from src.api.limiter import limiter

logger = get_logger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title="Intelligence ITA API",
    description="REST API for Intelligence ITA platform - Dashboard, Reports, and Map visualization",
    version="1.1.0"
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Configure CORS — configurable via env var for production
ALLOWED_ORIGINS = os.getenv(
    "ALLOWED_ORIGINS",
    "http://localhost:3000,http://localhost:3001,http://localhost:3002"
).split(",")

# GZip compression — reduces GeoJSON payloads ~90% (3MB → ~300KB)
app.add_middleware(GZipMiddleware, minimum_size=500)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-API-Key"],
)

# Include routers
app.include_router(dashboard.router)
app.include_router(reports.router)
app.include_router(stories.router)
app.include_router(oracle.router)
app.include_router(map_router.router)


# ===================================================================
# API Endpoints
# ===================================================================

@app.get("/")
@limiter.limit("10/minute")
async def root(request: Request):
    """API root endpoint"""
    return {
        "name": "Intelligence ITA API",
        "status": "running",
    }


# NOTE: Map endpoints moved to src/api/routers/map.py
# The /api/v1/map/* routes are now served by map_router


@app.get("/health")
@limiter.limit("10/minute")
async def health_check(request: Request):
    """Health check endpoint"""
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
