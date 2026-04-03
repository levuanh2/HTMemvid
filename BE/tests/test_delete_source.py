import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path


class DeleteSourceTestCase(unittest.TestCase):
    def setUp(self):
        # Tạo temp dir và patch đường dẫn index/memory để tránh đụng dữ liệu thật
        self.tmpdir = Path(tempfile.mkdtemp())

        # Patch faiss_utils paths
        import faiss_utils
        self._orig_meta_path = faiss_utils.META_PATH
        self._orig_index_path = faiss_utils.INDEX_PATH
        faiss_utils.META_PATH = str(self.tmpdir / "index.json")
        faiss_utils.INDEX_PATH = str(self.tmpdir / "index.faiss")

        # Patch memory_tree paths
        import memory_tree
        self._orig_memory_dir = memory_tree.MEMORY_DIR
        self._orig_trees_path = memory_tree.MEMORY_TREES_PATH
        self._orig_mem_index = memory_tree.MEMORY_INDEX_PATH
        self._orig_mem_meta = memory_tree.MEMORY_INDEX_META_PATH

        memory_tree.MEMORY_DIR = self.tmpdir / "memory"
        memory_tree.MEMORY_TREES_PATH = memory_tree.MEMORY_DIR / "memory_trees.json"
        memory_tree.MEMORY_INDEX_PATH = memory_tree.MEMORY_DIR / "memory_index.faiss"
        memory_tree.MEMORY_INDEX_META_PATH = memory_tree.MEMORY_DIR / "memory_index.json"
        os.makedirs(memory_tree.MEMORY_DIR, exist_ok=True)

    def tearDown(self):
        # Restore paths
        import faiss_utils
        import memory_tree

        faiss_utils.META_PATH = self._orig_meta_path
        faiss_utils.INDEX_PATH = self._orig_index_path

        memory_tree.MEMORY_DIR = self._orig_memory_dir
        memory_tree.MEMORY_TREES_PATH = self._orig_trees_path
        memory_tree.MEMORY_INDEX_PATH = self._orig_mem_index
        memory_tree.MEMORY_INDEX_META_PATH = self._orig_mem_meta

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_delete_existing_source(self):
        import faiss_utils
        import memory_tree

        # Tạo metadata index cho 2 source khác nhau
        meta = {
            "0": {"text": "chunk A1", "video": "sourceA_20250101_120000.mp4"},
            "1": {"text": "chunk A2", "video": "sourceA_20250101_120000.mp4"},
            "2": {"text": "chunk B1", "video": "sourceB_20250101_120000.mp4"},
        }
        with open(faiss_utils.META_PATH, "w", encoding="utf-8") as f:
            json.dump(meta, f)

        # Tạo memory_trees với 2 tree
        trees = [
            {
                "tree_id": "memtree_sourcea",
                "source_stem": "sourceA",
                "nodes": [{"memory_id": "mem_doc_sourcea"}, {"memory_id": "mem_sec_sourcea_0"}],
            },
            {
                "tree_id": "memtree_sourceb",
                "source_stem": "sourceB",
                "nodes": [{"memory_id": "mem_doc_sourceb"}],
            },
        ]
        with open(memory_tree.MEMORY_TREES_PATH, "w", encoding="utf-8") as f:
            json.dump(trees, f)

        # Gọi hàm delete trực tiếp
        deleted_chunks = faiss_utils.delete_chunks_by_source("sourceA")
        deleted_nodes = memory_tree.delete_memory_tree_by_source("sourceA")

        self.assertEqual(deleted_chunks, 2)
        self.assertEqual(deleted_nodes, 2)

        # Đảm bảo meta còn lại chỉ thuộc sourceB
        with open(faiss_utils.META_PATH, encoding="utf-8") as f:
            remaining_meta = json.load(f)
        self.assertEqual(len(remaining_meta), 1)
        self.assertEqual(
            faiss_utils._normalize_source_id(remaining_meta["2"]["video"]),
            faiss_utils._normalize_source_id("sourceB"),
        )

        with open(memory_tree.MEMORY_TREES_PATH, encoding="utf-8") as f:
            remaining_trees = json.load(f)
        self.assertEqual(len(remaining_trees), 1)
        self.assertEqual(remaining_trees[0]["source_stem"], "sourceB")

    def test_delete_non_existing_source(self):
        import faiss_utils
        import memory_tree

        # Tạo metadata index với 1 source
        meta = {
            "0": {"text": "chunk A1", "video": "sourceA_20250101_120000.mp4"},
        }
        with open(faiss_utils.META_PATH, "w", encoding="utf-8") as f:
            json.dump(meta, f)

        # Tạo memory_trees với 1 tree
        trees = [
            {
                "tree_id": "memtree_sourcea",
                "source_stem": "sourceA",
                "nodes": [{"memory_id": "mem_doc_sourcea"}],
            }
        ]
        with open(memory_tree.MEMORY_TREES_PATH, "w", encoding="utf-8") as f:
            json.dump(trees, f)

        deleted_chunks = faiss_utils.delete_chunks_by_source("non_exist_source")
        deleted_nodes = memory_tree.delete_memory_tree_by_source("non_exist_source")

        # Không xóa gì
        self.assertEqual(deleted_chunks, 0)
        self.assertEqual(deleted_nodes, 0)

        with open(faiss_utils.META_PATH, encoding="utf-8") as f:
            remaining_meta = json.load(f)
        self.assertEqual(len(remaining_meta), 1)

        with open(memory_tree.MEMORY_TREES_PATH, encoding="utf-8") as f:
            remaining_trees = json.load(f)
        self.assertEqual(len(remaining_trees), 1)


if __name__ == "__main__":
    unittest.main()


