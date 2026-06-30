import os
import cv2
import qrcode
import numpy as np
from typing import List
from datetime import datetime
from pathlib import Path
import hashlib

# DATA_DIR=/app (docker-compose) hoặc mặc định chạy local
from shared.paths import BE_ROOT
DATA_DIR_DEFAULT = str(BE_ROOT)
DATA_DIR = Path(os.environ.get("DATA_DIR", DATA_DIR_DEFAULT))
VIDEO_DIR = Path(os.environ.get("VIDEO_DIR", str(DATA_DIR / "videos")))
VIDEOS_DIR = str(VIDEO_DIR)
QR_FRAME_RATE = 1

# Tạo thư mục videos nếu chưa tồn tại
os.makedirs(VIDEOS_DIR, exist_ok=True)

# def generate_qr_frames(chunks: List[str]) -> List[np.ndarray]:
#     frames: List[np.ndarray] = []
#     for txt in chunks:
#         qr = qrcode.QRCode(
#             version=None,  # None + fit=True = auto chọn version hợp lệ (1–40)
#             error_correction=qrcode.constants.ERROR_CORRECT_L,
#             box_size=10,
#             border=4,
#         )
#         qr.add_data(txt)
#         qr.make(fit=True)
#
#         qr_img = qr.make_image(fill_color="black", back_color="white")
#         resized = qr_img.resize((512, 512))
#         frame = cv2.cvtColor(np.array(resized.convert("RGB")), cv2.COLOR_RGB2BGR)
#
#         frames.append(frame)
#     return frames

# Lưu các frame thành video MP4 với tên động
def _to_bgr_uint8(frame: np.ndarray) -> np.ndarray:
    """Chuẩn hoá frame về BGR uint8 cho VideoWriter."""
    if frame.dtype != np.uint8:
        frame = frame.astype(np.uint8)
    if frame.ndim == 3 and frame.shape[2] == 4:
        return cv2.cvtColor(frame, cv2.COLOR_RGBA2BGR)
    return frame


def _video_is_valid(path: str) -> bool:
    """File video coi là HỢP LỆ khi tồn tại, đủ lớn, và đọc lại được ≥1 frame.
    Tin cậy hơn writer.isOpened()/write() (headless: open OK nhưng không encode)."""
    try:
        if not os.path.exists(path) or os.path.getsize(path) < 1024:
            return False
        cap = cv2.VideoCapture(path)
        ok, _ = cap.read()
        cap.release()
        return bool(ok)
    except Exception:
        return False


def save_qr_frames_to_video(frames: List[np.ndarray], prefix: str = 'memory') -> str:
    """Lưu video QR. Chọn codec bằng cách GHI THỬ rồi KIỂM TRA file đọc được —
    headless (opencv-python-headless) thường mở writer OK nhưng không encode được
    (0 frame). Ghép codec↔container đúng: mp4v/avc1→.mp4, MJPG/XVID→.avi.
    """
    if not frames:
        raise ValueError("No frames to save")

    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    safe_prefix = "".join(c if c.isalnum() or c in ('_', '-') else '_' for c in prefix)
    height, width = frames[0].shape[:2]
    bgr = [_to_bgr_uint8(f) for f in frames]

    # (codec, đuôi container ăn khớp). Ưu tiên .mp4 (mp4v/avc1), fallback .avi (MJPG/XVID).
    candidates = [('mp4v', '.mp4'), ('avc1', '.mp4'), ('MJPG', '.avi'), ('XVID', '.avi')]
    tried: list[str] = []
    for codec_str, ext in candidates:
        out_path = f"{VIDEOS_DIR}/{safe_prefix}_{ts}{ext}"
        tried.append(codec_str)
        writer = None
        try:
            fourcc = cv2.VideoWriter_fourcc(*codec_str)  # type: ignore[attr-defined]
            writer = cv2.VideoWriter(out_path, fourcc, QR_FRAME_RATE, (width, height), isColor=True)
            if not writer.isOpened():
                writer.release()
                continue
            for frame in bgr:
                writer.write(frame)
            writer.release()
        except Exception as e:
            print(f"[video] codec '{codec_str}' lỗi: {e}")
            try:
                if writer is not None:
                    writer.release()
            except Exception:
                pass
            continue

        if _video_is_valid(out_path):
            print(f"[video] saved {len(frames)} frames via '{codec_str}' -> {out_path}")
            return out_path
        # file hỏng (0 frame) → xoá, thử codec/đuôi khác
        try:
            os.remove(out_path)
        except OSError:
            pass

    raise RuntimeError(f"Không codec nào ghi được video hợp lệ (đã thử {tried})")

def decode_video_qr(path: str) -> List[str]:
    cap = cv2.VideoCapture(path)
    detector = cv2.QRCodeDetector()
    decoded_texts: set[str] = set()  # Use set to avoid duplicates
    idx = 0

    QR_METADATA_PREFIX = "[METADATA:"
    QR_METADATA_SUFFIX = "]"

    def _compute_checksum(text: str) -> str:
        return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]

    def _extract_metadata(decoded: str) -> tuple[dict, str] | tuple[None, None]:
        if not decoded.startswith(QR_METADATA_PREFIX):
            return None, None
        end_pos = decoded.find(QR_METADATA_SUFFIX)
        if end_pos == -1:
            return None, None
        meta_str = decoded[len(QR_METADATA_PREFIX):end_pos]
        parts = meta_str.split(',')
        meta = {}
        for part in parts:
            if '=' in part:
                k, v = part.split('=', 1)
                meta[k.strip()] = v.strip()
        chunk_text = decoded[end_pos + 1:].strip()  # bỏ ']' + khoảng trắng
        return meta, chunk_text

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        text, points, _ = detector.detectAndDecode(frame)
        if text:
            meta, chunk_text = _extract_metadata(text)
            # Backward compatible: nếu không có metadata checksum thì vẫn chấp nhận
            if meta is None or meta.get("checksum") is None:
                decoded_texts.add(text)
            else:
                expected = _compute_checksum(chunk_text)
                if str(meta.get("checksum")) == expected:
                    decoded_texts.add(text)
                else:
                    print(f"[QR] checksum mismatch (skip) expected={expected} got={meta.get('checksum')}")
        idx += 1

    cap.release()
    return list(decoded_texts)