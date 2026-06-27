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

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/videos /app/index /app/memory /app/input_docs

# Cache embedding model trong image để hạn chế download lúc runtime.
# (Không ép offline; nếu cần offline hoàn toàn, set thêm TRANSFORMERS_OFFLINE=1 khi chạy.)
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2', cache_folder='/app/models')"
ENV SENTENCE_TRANSFORMERS_HOME=/app/models

EXPOSE 8080

ENV DATA_DIR=/app

CMD ["sh", "-c", "gunicorn -w ${WEB_CONCURRENCY:-1} -b 0.0.0.0:${PORT:-8080} --timeout ${GUNICORN_TIMEOUT:-300} app.main:app"]
