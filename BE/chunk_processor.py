# chunk_processor.py
import os
import re
import json
import hashlib
from typing import List, Dict, Tuple
from datetime import datetime

import numpy as np
import qrcode
from qrcode import constants
import cv2

from faiss_utils import append_to_index
from video_utils import save_qr_frames_to_video  # giữ nguyên hàm cũ để lưu video

# Ngưỡng an toàn cho QR code (version 40, error correction L)
MAX_QR_BYTES = 2953  # tối đa byte
MAX_QR_CHARS = 2300  # ước lượng an toàn cho tiếng Việt/Anh (khoảng 80-85% capacity)

# Định dạng prefix metadata trong text QR – dễ parse, khó conflict
QR_METADATA_PREFIX = "[METADATA:"
QR_METADATA_SUFFIX = "]"
def _make_metadata_string(parent_id:str,order:int,total:int,video_name:str,timestamp:str)->str:
    """
        Tạo chuỗi metadata chuẩn để nhúng vào đầu text QR
        Ví dụ: [METADATA:parent=1000,order=1/5,video=doc.mp4,ts=2025-12-17T12:34:56]
    """
    return f"{QR_METADATA_PREFIX}parent={parent_id},order={order},video={video_name},ts={timestamp}"
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
def process_and_store_chunks(chunks:list[str],video_name:str,timestamp:str)->tuple[str,list[Dict]]:
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

    # Danh sách frames QR và metadata entries
    qr_frames = []
    metadata_entries = []
    current_global_id = 0  # sẽ dùng để tạo ID cha và sub

    # Load existing meta để lấy ID tiếp theo
    from faiss_utils import _load_meta
    meta = _load_meta()
    existing_ids = [int(k.split('-')[0]) for k in meta.keys() if '-' in k or k.isdigit()]
    next_parent_id = max(existing_ids + [0]) + 1 if existing_ids else 0

    for chunk in chunks:
        current_global_id = next_parent_id
        parent_id_str = str(current_global_id)

        # Kiểm tra chunk có cần chia không
        if len(chunk.encode('utf-8')) <= MAX_QR_CHARS:
            # Chunk bình thường, không chia
            prefixed_text = _make_metadata_string(
                parent_id=parent_id_str,
                order=1,
                total=1,
                video_name=video_name,
                timestamp=timestamp
            ) + " " + chunk

            qr = qrcode.QRCode(
                version=None,
                error_correction=constants.ERROR_CORRECT_L,
                box_size=10,
                border=4,
            )
            qr.add_data(prefixed_text)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            # Fix kích thước frame cố định để VideoWriter ổn định
            target_size = (768, 768)  # 768x768 rõ QR, VideoWriter dễ chịu
            resized_img = img.resize(target_size)
            frame = cv2.cvtColor(np.array(resized_img.convert("RGB")), cv2.COLOR_RGB2BGR)
            qr_frames.append(frame)

            # Metadata entry cho chunk bình thường
            metadata_entries.append({
                "text": chunk,  # text gốc, không có prefix
                "video": video_name,
                "timestamp": timestamp,
                "parent_id": None,
                "sub_order": None,
                "total_parts": None,
                "is_subchunk": False
            })

        else:
            # Chia thành sub-chunk
            sub_texts = _split_long_chunk(chunk, MAX_QR_CHARS - 300)  # trừ đi chỗ cho prefix
            total = len(sub_texts)

            for idx, sub_text in enumerate(sub_texts, start=1):
                prefixed_text = _make_metadata_string(
                    parent_id=parent_id_str,
                    order=idx,
                    total=total,
                    video_name=video_name,
                    timestamp=timestamp
                ) + " " + sub_text

                qr = qrcode.QRCode(
                    version=None,
                    error_correction=constants.ERROR_CORRECT_L,
                    box_size=10,
                    border=4,
                )
                qr.add_data(prefixed_text)
                qr.make(fit=True)
                img = qr.make_image(fill_color="black", back_color="white")
                # Fix kích thước frame cố định
                target_size = (768, 768)
                resized_img = img.resize(target_size)
                frame = cv2.cvtColor(np.array(resized_img.convert("RGB")), cv2.COLOR_RGB2BGR)
                qr_frames.append(frame)

                # Metadata cho sub-chunk
                metadata_entries.append({
                    "text": sub_text,
                    "video": video_name,
                    "timestamp": timestamp,
                    "parent_id": parent_id_str,
                    "sub_order": idx,
                    "total_parts": total,
                    "is_subchunk": True
                })

        next_parent_id += 1

    # Lưu video
    video_path = save_qr_frames_to_video(qr_frames, prefix=os.path.splitext(video_name)[0])

    return video_path, metadata_entries
