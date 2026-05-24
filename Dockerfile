FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py ./
COPY scripts/ ./scripts/
COPY video_selection_agent/ ./video_selection_agent/
COPY comment_filtering_agent/ ./comment_filtering_agent/
COPY templates/ ./templates/
COPY seeds/ ./seeds/

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/products', timeout=5).status==200 else 1)" || exit 1

CMD ["python", "main.py"]
