import os
import cv2
import qrcode
import numpy as np
from typing import List
from datetime import datetime

VIDEOS_DIR = 'videos'
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
def save_qr_frames_to_video(frames: List[np.ndarray], prefix: str = 'memory') -> str:
    """
    Lưu video QR với các fix dành riêng cho Windows
    """
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    # FIX 1: Thay khoảng trắng và ký tự đặc biệt trong tên file
    safe_prefix = "".join(c if c.isalnum() or c in ('_', '-') else '_' for c in prefix)
    out_path = f"{VIDEOS_DIR}/{safe_prefix}_{ts}.mp4"
    print(f"Saving video to: {out_path}")

    if not frames:
        raise ValueError("No frames to save")

    height, width = frames[0].shape[:2]
    print(f"Frame size: {width}x{height}, Total frames: {len(frames)}")

    # Ưu tiên codec ổn định trên Windows
    codecs_to_try = ['XVID', 'DIVX', 'MJPG', 'mp4v']
    writer = None

    for codec_str in codecs_to_try:
        try:
            fourcc = cv2.VideoWriter_fourcc(*codec_str)  # type: ignore[attr-defined]
            writer = cv2.VideoWriter(
                out_path,
                fourcc,
                QR_FRAME_RATE,
                (width, height),
                isColor=True
            )

            if writer.isOpened():
                print(f"✓ Success: Using codec '{codec_str}'")
                break
        except Exception as e:
            print(f"✗ Exception with codec '{codec_str}': {e}")

    if writer is None or not writer.isOpened():
        raise RuntimeError(f"Cannot initialize VideoWriter for {out_path}")

    success_count = 0
    for i, frame in enumerate(frames):
        # FIX 2: Đảm bảo frame là uint8 và BGR
        if frame.dtype != np.uint8:
            frame = frame.astype(np.uint8)
        if frame.shape[2] == 3:  # BGR
            frame_to_write = frame
        else:
            frame_to_write = cv2.cvtColor(frame, cv2.COLOR_RGBA2BGR)

        ret = writer.write(frame_to_write)
        if ret:
            success_count += 1
        else:
            print(f"Warning: Failed to write frame {i}")

    writer.release()
    print(f"Completed: {success_count}/{len(frames)} frames written successfully")

    return out_path

def decode_video_qr(path: str) -> List[str]:
    cap = cv2.VideoCapture(path)
    detector = cv2.QRCodeDetector()
    decoded_texts: set = set()  # Use set to avoid duplicates
    idx = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        text, points, _ = detector.detectAndDecode(frame)
        if text:
            decoded_texts.add(text)
        idx += 1

    cap.release()
    return list(decoded_texts)