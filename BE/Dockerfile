FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

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

# ✅ SỬA Ở ĐÂY
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ✅ SỬA Ở ĐÂY
COPY . .

RUN mkdir -p /app/videos /app/index /app/memory /app/input_docs

EXPOSE 5000

ENV DATA_DIR=/app

CMD ["sh", "-c", "gunicorn -w ${WEB_CONCURRENCY:-1} -b 0.0.0.0:${PORT:-5000} --timeout ${GUNICORN_TIMEOUT:-300} --pythonpath BE main:app"]