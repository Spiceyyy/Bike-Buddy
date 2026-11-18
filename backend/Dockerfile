# Two targets: "dev" (tests, live reload via a bind mount) and "prod".

FROM python:3.12-slim AS base
WORKDIR /app
# libexpat is needed by osmium, which the scoring pipeline uses to read OSM files.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libexpat1 \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt requirements-pipeline.txt ./
# The pipeline runs inside this image too, so production can rebuild the scenery.
RUN pip install --no-cache-dir -r requirements.txt -r requirements-pipeline.txt
EXPOSE 8000

FROM base AS prod
COPY app ./app
COPY pipeline ./pipeline
RUN useradd --create-home --uid 10001 app
USER app
# One worker: the work is async and I/O-bound, and rate limits are kept per process.
# --proxy-headers takes the client address from the reverse proxy in front.
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]

FROM base AS dev
COPY requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY . .
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
