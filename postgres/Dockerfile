# PostGIS plus pgvector, for place embeddings (search_places).
FROM postgis/postgis:16-3.4

# This image is Debian bullseye, whose PostgreSQL apt repository has moved to
# the archive server.
RUN sed -i 's#http://apt.postgresql.org/pub/repos/apt#http://apt-archive.postgresql.org/pub/repos/apt#' \
        /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-16-pgvector \
    && rm -rf /var/lib/apt/lists/*
