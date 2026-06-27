"""
Phase 4 (kế hoạch): ParentDocumentRetriever có thể mở rộng tại đây.

Hiện tại hệ thống vẫn dùng `memory_tree.py` (FAISS memory index + tóm tắt LLM).
Re-export để import thống nhất theo roadmap.
"""

from app.domains.memory.tree import build_memory_tree_for_sources, query_with_memory_tree
__all__ = ["build_memory_tree_for_sources", "query_with_memory_tree"]
