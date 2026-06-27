import hashlib
import os
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.domains.vectorstore.store import append_to_index
from app.domains.ingest.video_utils import decode_video_qr
QR_METADATA_PREFIX = "[METADATA:"
QR_METADATA_SUFFIX = "]"


def _parse_qr_text(decoded_text: str) -> Tuple[Optional[Dict[str, str]], Optional[str]]:
    """
    Parse decoded QR payload:
    [METADATA:parent=...,order=...,video=...,ts=...,checksum=...] <chunk_text>
    """
    if not decoded_text or not isinstance(decoded_text, str):
        return None, None
    if not decoded_text.startswith(QR_METADATA_PREFIX):
        return None, None

    end_pos = decoded_text.find(QR_METADATA_SUFFIX)
    if end_pos == -1:
        return None, None

    meta_str = decoded_text[len(QR_METADATA_PREFIX):end_pos]
    chunk_text = decoded_text[end_pos + 1:].strip()

    meta: Dict[str, str] = {}
    for part in (meta_str.split(",")):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        meta[k.strip()] = v.strip()

    return meta, chunk_text


def _checksum16(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def _order_to_tuple(order_raw: Optional[str], fallback_rank: int) -> Tuple[int, int, int]:
    """
    Convert order like:
      - "3" -> (3, 0, fallback_rank)
      - "2.5" -> (2, 5, fallback_rank)
    """
    if not order_raw:
        return (10**9, 0, fallback_rank)
    s = str(order_raw).strip()
    if not s:
        return (10**9, 0, fallback_rank)
    parts = s.split(".")
    try:
        a = int(parts[0])
        b = int(parts[1]) if len(parts) > 1 else 0
        return (a, b, fallback_rank)
    except Exception:
        return (10**9, 0, fallback_rank)


def rebuild_faiss_index_from_videos(
    progress_cb: Optional[Callable[[int, Optional[Dict[str, Any]]], None]] = None
) -> Dict[str, Any]:
    """
    Video-as-source-of-truth:
    - Decode all MP4 videos in VIDEO_DIR
    - Reconstruct original chunks by grouping QR frames by parent_id and sorting by order
    - Rebuild /app/index/index.faiss and index.json from reconstructed chunk texts
    """
    from shared.paths import BE_ROOT
    data_dir = Path(os.environ.get("DATA_DIR", str(BE_ROOT)))
    video_dir = Path(os.environ.get("VIDEO_DIR", str(data_dir / "videos")))
    index_dir = Path(os.environ.get("INDEX_DIR", str(data_dir / "index")))

    index_dir.mkdir(parents=True, exist_ok=True)

    index_faiss = index_dir / "index.faiss"
    index_json = index_dir / "index.json"
    backup_faiss = index_dir / "index_backup.faiss"
    backup_json = index_dir / "index_backup.json"

    def _report(progress: int, extra: Optional[Dict[str, Any]] = None) -> None:
        if not progress_cb:
            return
        try:
            progress_cb(int(progress), extra or None)
        except Exception:
            # never crash rebuild because of progress reporting
            pass

    print("[REBUILD] start video->index")
    video_files = sorted(video_dir.glob("*.mp4"))
    num_videos = len(video_files)
    print(f"[REBUILD] found videos: {num_videos} in {video_dir}")
    _report(0, {"num_videos": num_videos})

    # Backup old index for safety
    if index_faiss.exists():
        shutil.copy2(index_faiss, backup_faiss)
    if index_json.exists():
        shutil.copy2(index_json, backup_json)

    # Groups: parent_id -> list[(order_tuple, chunk_text, video_name)]
    grouped: Dict[str, List[Tuple[Tuple[int, int, int], str, str]]] = defaultdict(list)
    decoded_count = 0
    used_count = 0
    skipped_count = 0

    try:
        # Stage A: decode videos -> progress [0..70]
        for i, v in enumerate(video_files):
            print(f"[REBUILD] decoding video: {v.name}")
            try:
                decoded_list = decode_video_qr(str(v))
            except Exception as exc:
                print(f"[REBUILD] decode failed video={v.name} err={exc}")
                continue

            for rank, decoded_text in enumerate(decoded_list):
                decoded_count += 1
                meta, chunk_text = _parse_qr_text(decoded_text)
                if not meta or chunk_text is None:
                    skipped_count += 1
                    continue

                parent_id = meta.get("parent")
                order_raw = meta.get("order")
                video_name = meta.get("video") or ""
                checksum = meta.get("checksum")

                if not parent_id or not video_name:
                    skipped_count += 1
                    continue

                if checksum:
                    expected = _checksum16(chunk_text)
                    if str(checksum) != expected:
                        # Skip wrong QR content
                        print(f"[REBUILD] checksum mismatch parent={parent_id} expected={expected} got={checksum}")
                        skipped_count += 1
                        continue

                order_tuple = _order_to_tuple(order_raw, fallback_rank=rank)
                grouped[parent_id].append((order_tuple, chunk_text, video_name))
                used_count += 1
            
            # Update coarse progress after each video
            if num_videos > 0:
                p = 5 + int((i + 1) / num_videos * 65)  # 5..70
                _report(p)

        # Reconstruct chunks
        reconstructed: List[str] = []
        custom_metadata: List[Dict[str, Any]] = []
        _report(75)

        # Keep deterministic output by sorting parent_id lexicographically as int when possible
        def _parent_sort_key(pid: str) -> int:
            try:
                return int(pid)
            except Exception:
                return 10**18

        for parent_id in sorted(grouped.keys(), key=_parent_sort_key):
            parts = grouped[parent_id]
            if not parts:
                continue
            parts.sort(key=lambda x: x[0])
            texts = [t.strip() for _, t, _ in parts if t and str(t).strip()]
            if not texts:
                continue
            merged_text = "\n\n".join(texts).strip()
            if not merged_text:
                continue

            # Use first video_name found for filtering
            video_name = parts[0][2]
            reconstructed.append(merged_text)
            custom_metadata.append({
                "video": video_name,
                # Preserve stable ordering cues for Memory Tree sorting
                "parent_id": str(parent_id),
                "sub_order": 1,
                "total_parts": 1,
                "is_subchunk": False,
            })

        print(f"[REBUILD] reconstructed chunks={len(reconstructed)} decoded={decoded_count} used={used_count} skipped={skipped_count}")
        if not reconstructed:
            raise RuntimeError("No reconstructed chunks from videos")
        _report(85, {"num_chunks": len(reconstructed)})

        # Delete current index before rebuild
        if index_faiss.exists():
            index_faiss.unlink()
        if index_json.exists():
            index_json.unlink()

        # Rebuild FAISS from reconstructed chunks (video name per chunk via custom_metadata['video'])
        _report(90)
        append_to_index(
            chunks=reconstructed,
            video_name="__rebuild_from_video__",
            custom_metadata=custom_metadata,
            batch_size=32,
        )

        print("[REBUILD] completed successfully")
        _report(100, {"num_chunks": len(reconstructed)})
        return {
            "status": "ok",
            "num_chunks": len(reconstructed),
            "num_videos": num_videos,
        }
    except Exception as exc:
        print(f"[REBUILD] failed err={exc} -> restoring backup")
        # Restore backups best-effort
        try:
            if backup_faiss.exists():
                shutil.copy2(backup_faiss, index_faiss)
            if backup_json.exists():
                shutil.copy2(backup_json, index_json)
        except Exception as restore_exc:
            print(f"[REBUILD] restore failed err={restore_exc}")
        raise

