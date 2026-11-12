FROM python:3.12-slim

WORKDIR /app
# libexpat is needed by osmium, which the scoring pipeline uses to read OSM files.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libexpat1 \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt requirements-dev.txt requirements-pipeline.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY . .

EXPOSE 8000
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
