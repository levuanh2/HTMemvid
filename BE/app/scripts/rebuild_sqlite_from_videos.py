"""Rebuild chunks.sqlite from index.json pointer + video decode (recovery)."""
import os
import sys

# Add BE to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.domains.vectorstore import chunk_text_store

if __name__ == "__main__":
    n = chunk_text_store.rebuild_from_videos()
    print(f"[rebuild_sqlite] rebuilt {n} chunk texts from video/inline")
