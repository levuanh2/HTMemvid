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

WORKDIR /srv/app

# Install Python deps
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Gunicorn cho production
RUN pip install --no-cache-dir gunicorn

# Copy backend source
COPY BE/ .

# Create runtime data directories (also backed by volumes in docker-compose)
RUN mkdir -p /app/videos /app/index /app/memory /app/input_docs

EXPOSE 5000

ENV DATA_DIR=/app

# Increase timeout because LLM/Ollama requests can be slow (avoid premature worker kill).
# Keep workers=1 to avoid in-memory job/cache inconsistency across multiple workers.
CMD ["gunicorn", "-w", "1", "-b", "0.0.0.0:5000", "--timeout", "300", "main:app"]

