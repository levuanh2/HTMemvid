import os
import cv2
import qrcode
import numpy as np
from typing import List, Optional
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
    candidates = [('mp4v', '.mp4'), ('avc1', '.mp4')]
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

def decode_video_qr(path: str) -> List[tuple[int, str]]:
    """Decode QR theo THỨ TỰ frame. Trả [(frame_index, chunk_text)]. Verify checksum nếu có
    (sai → bỏ frame đó nhưng KHÔNG đổi frame_index của frame khác)."""
    cap = cv2.VideoCapture(path)
    detector = cv2.QRCodeDetector()
    QR_METADATA_PREFIX, QR_METADATA_SUFFIX = "[METADATA:", "]"

    def _checksum(text: str) -> str:
        return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]

    def _extract(decoded: str):
        if not decoded.startswith(QR_METADATA_PREFIX):
            return None, decoded.strip()
        end = decoded.find(QR_METADATA_SUFFIX)
        if end == -1:
            return None, decoded.strip()
        meta = {}
        for part in decoded[len(QR_METADATA_PREFIX):end].split(','):
            if '=' in part:
                k, v = part.split('=', 1); meta[k.strip()] = v.strip()
        return meta, decoded[end + 1:].strip()

    out: List[tuple[int, str]] = []
    idx = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        text, _pts, _ = detector.detectAndDecode(frame)
        if text:
            meta, chunk_text = _extract(text)
            ok = (not meta) or (meta.get("checksum") is None) or (str(meta.get("checksum")) == _checksum(chunk_text))
            if ok:
                out.append((idx, chunk_text))
        idx += 1
    cap.release()
    return out


def decode_frame(path: str, frame_index: int) -> Optional[str]:
    """Decode 1 frame theo index (recovery on-demand)."""
    cap = cv2.VideoCapture(path)
    try:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(frame_index))
        ret, frame = cap.read()
        if not ret:
            return None
        detector = cv2.QRCodeDetector()
        text, _pts, _ = detector.detectAndDecode(frame)
        if not text:
            return None
        end = text.find("]")
        return text[end + 1:].strip() if text.startswith("[METADATA:") and end != -1 else text.strip()
    finally:
        cap.release()