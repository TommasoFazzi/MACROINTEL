FROM python:3.12-slim-bookworm

# System deps for psycopg2, lxml, spaCy + weasyprint (PDF rendering)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libpq-dev libxml2-dev libxslt-dev \
    curl ca-certificates git \
    libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b \
    libfontconfig1 libgobject-2.0-0 \
    libcairo2 libgdk-pixbuf-2.0-0 libffi-dev && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps (production only, no streamlit/openbb/dev tools)
# BuildKit cache mount: pip cache persists across builds → no re-download on requirements change
COPY requirements-prod.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements-prod.txt

# Pre-download spaCy model
RUN python -m spacy download xx_ent_wiki_sm

# Pre-download sentence-transformers model into image layer
RUN python -c "from sentence_transformers import SentenceTransformer; \
    SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')"

# Pre-download cross-encoder model for report generation
RUN python -c "from sentence_transformers import CrossEncoder; \
    CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')"

# Pre-trigger OpenBB auto-build as root so .build.lock is writable at runtime
RUN python -c "from openbb import obb" || true

# Copy application code
COPY src/ ./src/
COPY scripts/ ./scripts/
COPY config/ ./config/
COPY migrations/ ./migrations/
COPY templates/ ./templates/
COPY deploy/entrypoint.sh ./deploy/entrypoint.sh

# Non-root user
RUN useradd -m -u 1001 appuser && \
    chmod +x ./deploy/entrypoint.sh && \
    chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

ENTRYPOINT ["./deploy/entrypoint.sh"]
