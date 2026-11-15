# The API image for `docker compose up`. The source is mounted in at run time (see compose.yaml),
# so this image only holds Python, the pinned packages, and what PDF output needs from the OS.
FROM python:3.13-slim

# WeasyPrint needs Pango, and a slim image ships no fonts at all.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 8000
CMD ["uvicorn", "orchestrator.api:app", "--host", "0.0.0.0", "--port", "8000", "--reload", "--reload-dir", "orchestrator"]
