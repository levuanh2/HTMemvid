"""
Seam cho Vector Store (FAISS).

Khớp với API công khai hiện có của vector_store.py:
  append_to_index / search_index / delete_chunks_by_source / rebuild_chunk_index / _load_meta.

Điểm quan trọng: thêm load_meta() như phương thức công khai để memory_tree và
retrieval/hybrid đọc index.json QUA interface thay vì mở file trực tiếp — gỡ
coupling-file ngầm mà vẫn giữ in-process (không qua mạng). vector-store CỐ Ý
không tách thành service (xem plan).
"""

from __future__ import annotations

from typing import Dict, List, Optional, Protocol, runtime_checkable


@runtime_checkable
class VectorStore(Protocol):
    def append(
        self,
        chunks: List[str],
        video_name: str = "",
        custom_metadata: Optional[List[Dict]] = None,
        batch_size: int = 32,
    ) -> None:
        """Thêm chunk vào index (mirror append_to_index)."""
        ...

    def search(self, query: str, k: int = 5) -> List[str]:
        """Tìm k chunk gần nhất, trả về list text (mirror search_index)."""
        ...

    def delete_source(self, source_id: str) -> int:
        """Xoá mọi chunk thuộc source, trả số chunk đã xoá (mirror delete_chunks_by_source)."""
        ...

    def load_meta(self) -> Dict[str, Dict]:
        """Đọc nội dung index.json (chunk_id -> {text, video, embedding, ...})."""
        ...

    def rebuild(self, existing_meta: Optional[Dict[str, Dict]] = None) -> None:
        """Dựng lại FAISS index từ metadata (mirror rebuild_chunk_index)."""
        ...
