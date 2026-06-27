# syntax=docker/dockerfile:1

# =========================================================================
# Stage 1 — builder: cài Python deps vào venv + tải sẵn model embedding.
# Toàn bộ thứ "nặng lúc build" (pip, download model) nằm ở stage này và
# KHÔNG đi vào image cuối ngoài venv + model đã build.
# =========================================================================
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# venv riêng để stage 2 chỉ cần COPY nguyên thư mục.
RUN python -m venv /opt/venv

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# Tải sẵn model embedding vào /opt/models (tránh download lúc runtime).
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2', cache_folder='/opt/models')"

# =========================================================================
# Stage 2 — runtime: base slim + chỉ thư viện hệ thống cần khi CHẠY,
# copy venv + model từ builder, copy source, sinh gRPC stub.
# =========================================================================
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    DATA_DIR=/app \
    SENTENCE_TRANSFORMERS_HOME=/opt/models

# Runtime libs: tesseract (OCR), libgl/glib (opencv), ffmpeg (video), curl (healthcheck).
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

# Lấy môi trường Python đã cài sẵn + model từ builder.
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /opt/models /opt/models

WORKDIR /app
COPY . .

# Sinh gRPC stub trong image (gen/ bị .dockerignore loại khỏi context) + tạo data dirs.
RUN python scripts/build_proto.py \
    && mkdir -p /app/videos /app/index /app/memory /app/input_docs

EXPOSE 8080

# Mặc định chạy monolith (Flask). docker-compose override `command:` cho
# llm-gateway (python -m services.llm_gateway.server) và mindmap-service.
CMD ["sh", "-c", "gunicorn -w ${WEB_CONCURRENCY:-1} -b 0.0.0.0:${PORT:-8080} --timeout ${GUNICORN_TIMEOUT:-300} app.main:app"]
