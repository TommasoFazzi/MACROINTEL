FROM pgvector/pgvector:pg17
RUN apt-get update && apt-get install -y \
    postgresql-17-postgis-3 \
    gdal-bin \
    && rm -rf /var/lib/apt/lists/*
COPY postgresql.conf /etc/postgresql/custom.conf
