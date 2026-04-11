FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# System dependencies:
# - Tesseract OCR + Vietnamese language pack
# - libgl1 for OpenCV
# - ffmpeg for video codecs
# - curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-vie \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps (copy requirements first for caching)
# Copy dependencies first (for cache)
COPY BE/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend source
COPY BE/ .

RUN mkdir -p /app/videos /app/index /app/memory /app/input_docs

EXPOSE 5000

ENV DATA_DIR=/app

# Run production server (Gunicorn).
# IMPORTANT: This codebase uses in-memory async job stores; multiple workers do not share job state.
# Keep WEB_CONCURRENCY=1 (default) to keep /query-status and /mindmap-status correct.
CMD ["sh", "-c", "gunicorn -w ${WEB_CONCURRENCY:-1} -b 0.0.0.0:5000 --timeout ${GUNICORN_TIMEOUT:-300} main:app"]

