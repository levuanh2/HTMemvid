# chunk_processor.py
import os
import re
import json
import hashlib
from typing import List, Dict, Tuple, Optional
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import qrcode
from qrcode import constants
import cv2

from app.domains.vectorstore.store import append_to_index
from app.domains.ingest.video_utils import save_qr_frames_to_video
  # giữ nguyên hàm cũ để lưu video
# Ngưỡng an toàn cho QR code (version 40, error correction L)
MAX_QR_BYTES = 2953  # tối đa byte
MAX_QR_CHARS = 2300  # ước lượng an toàn cho tiếng Việt/Anh (khoảng 80-85% capacity)
# Giới hạn thực tế khi đã có prefix metadata (đã trừ buffer cho metadata + checksum)
SAFE_CHUNK_CHARS = 1700  # Đảm bảo sau khi thêm prefix vẫn < MAX_QR_CHARS

# Định dạng prefix metadata trong text QR – dễ parse, khó conflict
QR_METADATA_PREFIX = "[METADATA:"
QR_METADATA_SUFFIX = "]"
def _compute_checksum(text: str) -> str:
    # CRC-like nhẹ: SHA256 nhưng cắt ngắn để tiết kiệm dung lượng QR
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]

def _make_metadata_string(parent_id: str, order: int | str, total: int | str, video_name: str, timestamp: str, checksum: str) -> str:
    """
        Tạo chuỗi metadata chuẩn để nhúng vào đầu text QR
        Ví dụ: [METADATA:parent=1000,order=1/5,video=doc.mp4,ts=2025-12-17T12:34:56]
    """
    return f"{QR_METADATA_PREFIX}parent={parent_id},order={order},video={video_name},ts={timestamp},checksum={checksum}"
def _extract_metadata_from_text(text:str)->dict[str, str]|None:
    """
        Parse metadata từ text QR nếu có prefix
        Trả về dict hoặc None nếu không có
    """
    if not text.startswith(QR_METADATA_PREFIX):
        return None
    try:
        end_pos=text.find(QR_METADATA_SUFFIX)
        if end_pos == -1:
            return None
        meta_str=text[len(QR_METADATA_PREFIX):end_pos]
        parts=meta_str.split(',')
        meta={}
        for part in parts:
            if '=' in part:
                k,v=part.split('=',1)
                meta[k.strip()]=v.strip()
        return meta
    except:
        return None
def _split_long_chunk(chunk_text:str,max_chars:int=MAX_QR_CHARS)->list[str] :
    """
        Chia chunk dài thành các sub-chunk nhỏ hơn max_chars
        Cố gắng chia theo câu hoàn chỉnh để giữ ngữ nghĩa
    """
    if len(chunk_text.encode('utf-8'))<=max_chars:
        return [chunk_text]
    sentences = re.split(r'(?<=[.!?])\s+', chunk_text)
    sub_chunks=[]
    current=[]
    current_bytes=0
    for sent in sentences:
        sent_bytes=len(sent.encode('utf-8'))
        if current_bytes+sent_bytes>max_chars and current:
            sub_chunks.append(" ".join(current).strip())
            current=[sent]
            current_bytes=sent_bytes
        else:
            current.append(sent)
            current_bytes+=sent_bytes
    if current:
        sub_chunks.append(" ".join(current).strip())
    return [sc for sc in sub_chunks if sc]
def _create_qr_frame(prefixed_text: str) -> Tuple[Optional[np.ndarray], bool]:
    """
    Tạo QR frame từ prefixed text. Trả về (frame, success).
    Thread-safe function để dùng với ThreadPoolExecutor.
    """
    try:
        prefixed_bytes = len(prefixed_text.encode('utf-8'))
        if prefixed_bytes > MAX_QR_CHARS:
            return None, False
        
        qr = qrcode.QRCode(
            version=None,
            error_correction=constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(prefixed_text)
        qr.make(fit=True)
        
        if hasattr(qr, 'version') and qr.version and qr.version > 40:
            return None, False
        
        img = qr.make_image(fill_color="black", back_color="white")
        target_size = (768, 768)
        resized_img = img.resize(target_size)
        frame = cv2.cvtColor(np.array(resized_img.convert("RGB")), cv2.COLOR_RGB2BGR)
        return frame, True
    except Exception as e:
        print(f"⚠️ QR creation failed: {e}")
        return None, False


def process_and_store_chunks(chunks:list[str],video_name:str,timestamp:str,max_workers:int=4)->tuple[str,list[Dict]]:
    """
        Hàm chính xử lý toàn bộ:
        - Chia chunk dài thành sub-chunk nếu cần
        - Thêm prefix metadata vào mỗi sub-chunk cho QR
        - Tạo QR frames
        - Lưu video
        - Chuẩn bị list metadata entries cho append_to_index
        - Trả về: video_path, list metadata_entries
    """
    if timestamp is None:
        timestamp = datetime.now().isoformat()

    # Danh sách tasks để xử lý song song
    qr_tasks = []  # List of (index, prefixed_text, metadata_entry)
    current_global_id = 0  # sẽ dùng để tạo ID cha và sub

    # Load existing meta để lấy ID tiếp theo
    from app.domains.vectorstore.store import _load_meta
    meta = _load_meta()
    existing_ids = [int(k.split('-')[0]) for k in meta.keys() if '-' in k or k.isdigit()]
    next_parent_id = max(existing_ids + [0]) + 1 if existing_ids else 0

    # Chuẩn bị tasks cho parallel processing
    task_index = 0
    for chunk_index, chunk in enumerate(chunks):
        current_global_id = next_parent_id
        parent_id_str = str(current_global_id)

        # Kiểm tra chunk có cần chia không (tính cả prefix metadata)
        # Ước lượng độ dài prefix: [METADATA:parent=...,order=...,video=...,ts=...,checksum=...]
        estimated_prefix_len = 220 + len(video_name) + len(timestamp)  # Ước lượng an toàn
        
        chunk_bytes = len(chunk.encode('utf-8'))
        estimated_total_bytes = chunk_bytes + estimated_prefix_len
        
        # Thử xử lý chunk nhỏ trước
        chunk_processed = False
        if estimated_total_bytes <= MAX_QR_CHARS:
            # Chunk bình thường, không chia
            checksum = _compute_checksum(chunk)
            prefixed_text = _make_metadata_string(
                parent_id=parent_id_str,
                order=1,
                total=1,
                video_name=video_name,
                timestamp=timestamp,
                checksum=checksum
            ) + " " + chunk

            # Thêm vào task list để xử lý song song
            metadata_entry = {
                "text": chunk,
                "video": video_name,
                "timestamp": timestamp,
                "parent_id": None,
                "sub_order": None,
                "total_parts": None,
                "is_subchunk": False,
                "chunk_index": chunk_index,
            }
            qr_tasks.append((task_index, prefixed_text, metadata_entry))
            task_index += 1
            chunk_processed = True
        
        # Nếu chunk chưa được xử lý (quá dài hoặc QR creation fail), chia thành sub-chunk
        if not chunk_processed:
            sub_texts = _split_long_chunk(chunk, SAFE_CHUNK_CHARS)
            total = len(sub_texts)

            for idx, sub_text in enumerate(sub_texts, start=1):
                checksum = _compute_checksum(sub_text)
                prefixed_text = _make_metadata_string(
                    parent_id=parent_id_str,
                    order=idx,
                    total=total,
                    video_name=video_name,
                    timestamp=timestamp,
                    checksum=checksum
                ) + " " + sub_text

                # Kiểm tra độ dài trước khi thêm vào task
                prefixed_bytes = len(prefixed_text.encode('utf-8'))
                if prefixed_bytes > MAX_QR_CHARS:
                    # Nếu vẫn quá dài, chia nhỏ hơn nữa
                    print(f"⚠️ Sub-chunk still too long ({prefixed_bytes} bytes), splitting further...")
                    mini_chunks = _split_long_chunk(sub_text, SAFE_CHUNK_CHARS // 2)
                    for mini_idx, mini_text in enumerate(mini_chunks, start=1):
                        mini_checksum = _compute_checksum(mini_text)
                        mini_prefixed = _make_metadata_string(
                            parent_id=parent_id_str,
                            order=f"{idx}.{mini_idx}",
                            total=f"{total}.{len(mini_chunks)}",
                            video_name=video_name,
                            timestamp=timestamp,
                            checksum=mini_checksum
                        ) + " " + mini_text
                        metadata_entry = {
                            "text": mini_text,
                            "video": video_name,
                            "timestamp": timestamp,
                            "parent_id": parent_id_str,
                            "sub_order": f"{idx}.{mini_idx}",
                            "total_parts": f"{total}.{len(mini_chunks)}",
                            "is_subchunk": True,
                            "chunk_index": chunk_index,
                        }
                        qr_tasks.append((task_index, mini_prefixed, metadata_entry))
                        task_index += 1
                else:
                    metadata_entry = {
                        "text": sub_text,
                        "video": video_name,
                        "timestamp": timestamp,
                        "parent_id": parent_id_str,
                        "sub_order": idx,
                        "total_parts": total,
                        "is_subchunk": True,
                        "chunk_index": chunk_index,
                    }
                    qr_tasks.append((task_index, prefixed_text, metadata_entry))
                    task_index += 1

        next_parent_id += 1

    # Xử lý QR frames song song với ThreadPoolExecutor
    qr_frames = [None] * len(qr_tasks)  # Pre-allocate để giữ thứ tự
    metadata_entries = [None] * len(qr_tasks)
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit tất cả tasks
        future_to_index = {
            executor.submit(_create_qr_frame, prefixed_text): (idx, metadata_entry)
            for idx, prefixed_text, metadata_entry in qr_tasks
        }
        
        # Collect results theo thứ tự
        for future in as_completed(future_to_index):
            idx, metadata_entry = future_to_index[future]
            try:
                frame, success = future.result()
                if success and frame is not None:
                    qr_frames[idx] = frame
                    metadata_entries[idx] = metadata_entry
            except Exception as e:
                print(f"⚠️ Error processing QR task {idx}: {e}")
    
    # Loại bỏ None entries (failed QR creations)
    qr_frames = [f for f in qr_frames if f is not None]
    metadata_entries = [e for e in metadata_entries if e is not None]
    
    for i, e in enumerate(metadata_entries):
        e["frame_index"] = i
    
    if not qr_frames:
        raise ValueError("No valid QR frames created")

    # Lưu video QR là lưu trữ PHỤ (text chunk đã nằm trong FAISS index). Ghi hỏng (headless
    # thiếu codec) KHÔNG được chặn indexing → nuốt lỗi, trả video_path rỗng, pipeline đi tiếp.
    try:
        video_path = save_qr_frames_to_video(qr_frames, prefix=os.path.splitext(video_name)[0])
    except Exception as e:
        print(f"⚠️ [chunk_processor] Lưu video thất bại (bỏ qua, vẫn index): {e}")
        video_path = ""

    return video_path, metadata_entries
