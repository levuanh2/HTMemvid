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

# KHÔNG pre-bake model embedding vào image nữa:
# - Bản cũ bake all-MiniLM-L6-v2 (~90MB) — SAI model: runtime (late chunking,
#   mặc định ON) dùng BAAI/bge-m3. MiniLM baked = dead weight, bge-m3 vẫn phải
#   tải lúc boot đầu.
# - Bake bge-m3 (~2.3GB) vào image thì quá nặng (image phình, mỗi rebuild kéo lại).
# → Chiến lược: HF cache trên volume `hf_cache` (docker-compose mount
#   /root/.cache/huggingface cho cả backend/llm-gateway/mindmap-service/rq-worker).
#   bge-m3 tải MỘT lần ở boot đầu rồi tồn tại qua restart/rebuild. Muốn seed
#   trước (offline env): chạy 1 lần
#   docker compose run --rm backend python -c "from app.domains.ingest.late_chunk import get_late_chunk_encoder; get_late_chunk_encoder().warmup()"

# =========================================================================
# Stage 2 — runtime: base slim + chỉ thư viện hệ thống cần khi CHẠY,
# copy venv + model từ builder, copy source, sinh gRPC stub.
# =========================================================================
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    DATA_DIR=/app

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

# Lấy môi trường Python đã cài sẵn từ builder. (Model embedding KHÔNG bake —
# xem chú thích ở builder stage: bge-m3 sống trên volume hf_cache.)
COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY . .

# Sinh gRPC stub trong image (gen/ bị .dockerignore loại khỏi context) + tạo data dirs.
RUN python scripts/build_proto.py \
    && mkdir -p /app/videos /app/index /app/memory /app/input_docs

EXPOSE 8080

# Mặc định chạy monolith (Flask). docker-compose override `command:` cho
# llm-gateway (python -m services.llm_gateway.server) và mindmap-service.
CMD ["sh", "-c", "gunicorn -w ${WEB_CONCURRENCY:-1} -b 0.0.0.0:${PORT:-8080} --timeout ${GUNICORN_TIMEOUT:-300} app.main:app"]
