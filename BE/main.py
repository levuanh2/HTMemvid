import os
import unicodedata
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

from ingest_utils import extract_text, split_text
from video_utils import  save_qr_frames_to_video
from faiss_utils import append_to_index, search_index, delete_source_from_index, MODEL_NAME
from ollama_utils import summarize_whole_document, summarize_results, SLM_MODEL
from mindmap_utils import get_main_branches, generate_mindmap_flat, generate_mindmap_cmgn
from chunk_processor import process_and_store_chunks
app = Flask(__name__)
CORS(
    app,
    resources={r"/*": {"origins": "*"}},
    methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type"]
)


BASE_DIR = Path(__file__).resolve().parent
VIDEOS_DIR = 'videos'
INPUT_DIR = 'input_docs'
MINDMAPS_PATH = BASE_DIR / 'mindmaps.json'
os.makedirs(INPUT_DIR, exist_ok=True)
os.makedirs(VIDEOS_DIR, exist_ok=True)


@app.get('/')
def home():
    return 'MemvidX API is running.'


def _load_mindmaps() -> list[dict]:
    if not MINDMAPS_PATH.exists():
        return []
    try:
        with open(MINDMAPS_PATH, encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except Exception as exc:
        print(f"⚠️ Không thể đọc mindmaps.json: {exc}")
    return []


def _save_mindmaps(records: list[dict]) -> None:
    try:
        tmp_path = MINDMAPS_PATH.with_suffix('.tmp')
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
        tmp_path.replace(MINDMAPS_PATH)
    except Exception as exc:
        print(f"⚠️ Không thể lưu mindmaps.json: {exc}")


def _append_mindmap(record: dict) -> None:
    records = _load_mindmaps()
    records.insert(0, record)
    _save_mindmaps(records)


def _mindmap_response(record: dict) -> dict:
    nodes = record.get("nodes")
    if not isinstance(nodes, list):
        nodes = []
    return {
        "id": record.get("id"),
        "title": record.get("title"),
        "nodes": nodes,
        "sources": record.get("sources", []),
        "createdAt": record.get("createdAt"),
        "strategy": record.get("strategy") or "iterative",
    }


# -------------------------
# 📤 Process raw text
# -------------------------
@app.post('/process-doc')
def process_doc():
    text = request.json.get('text', '')
    if not text:
        return jsonify({'error': 'Missing text'}), 400

    chunks = split_text(text)

    # Thay toàn bộ logic cũ bằng hàm mới
    video_name = "raw_text"  # hoặc tạo tên có nghĩa hơn
    video_path, metadata_entries = process_and_store_chunks(
        chunks=chunks,
        video_name=video_name,
        timestamp=datetime.now().isoformat()
    )

    # Append từng entry với custom metadata
    for entry in metadata_entries:
        append_to_index(
            chunks=[entry["text"]],
            video_name=video_path,
            custom_metadata=[{
                "parent_id": entry.get("parent_id"),
                "sub_order": entry.get("sub_order"),
                "total_parts": entry.get("total_parts"),
                "is_subchunk": entry.get("is_subchunk", False)
            }]
        )

    return jsonify({'video_path': video_path})


# -------------------------
# 📤 Upload single file
# -------------------------
@app.post('/upload-file')
def upload_file():
    file = request.files.get('file')
    if not file:
        return jsonify({'error': 'Missing file'}), 400

    save_path = os.path.join(INPUT_DIR, file.filename)
    file.save(save_path)

    text = extract_text(save_path)
    if not text.strip():
        return jsonify({'error': 'Cannot read file content'}), 400

    chunks = split_text(text)

    # Tạo tên video từ filename gốc (giống cũ)
    video_name = f"{file.filename.replace('.', '_')}"

    video_path, metadata_entries = process_and_store_chunks(
        chunks=chunks,
        video_name=video_name,
        timestamp=datetime.now().isoformat()
    )

    # Append từng entry
    for entry in metadata_entries:
        append_to_index(
            chunks=[entry["text"]],
            video_name=video_path,
            custom_metadata=[{
                "parent_id": entry.get("parent_id"),
                "sub_order": entry.get("sub_order"),
                "total_parts": entry.get("total_parts"),
                "is_subchunk": entry.get("is_subchunk", False)
            }]
        )

    return jsonify({'video_path': video_path, 'message': 'File processed and index built'})

# -------------------------
# 📤 Upload multiple files
# -------------------------
@app.post('/upload-multiple')
def upload_multiple():
    files = request.files.getlist('files')
    if not files:
        return jsonify({'error': 'Missing files'}), 400

    results = []
    for file in files:
        save_path = os.path.join(INPUT_DIR, file.filename)
        file.save(save_path)

        text = extract_text(save_path)
        if not text.strip():
            results.append({'file': file.filename, 'error': 'Cannot read content'})
            continue

        chunks = split_text(text)
        video_name = f"{file.filename.replace('.', '_')}"

        try:
            video_path, metadata_entries = process_and_store_chunks(
                chunks=chunks,
                video_name=video_name,
                timestamp=datetime.now().isoformat()
            )

            for entry in metadata_entries:
                append_to_index(
                    chunks=[entry["text"]],
                    video_name=video_path,
                    custom_metadata=[{
                        "parent_id": entry.get("parent_id"),
                        "sub_order": entry.get("sub_order"),
                        "total_parts": entry.get("total_parts"),
                        "is_subchunk": entry.get("is_subchunk", False)
                    }]
                )

            results.append({
                'file': file.filename,
                'video_path': video_path,
                'message': 'OK'
            })
        except Exception as e:
            results.append({
                'file': file.filename,
                'error': f'Processing failed: {str(e)}'
            })

    return jsonify({'results': results})
@app.get('/list-indexed')
def list_indexed():
    try:
        with open('index/index.json', encoding='utf-8') as f:
            meta = json.load(f)

        video_map = {}
        for item in meta.values():
            video = unicodedata.normalize('NFKD', item.get('video', '').strip()).replace('\u00a0', ' ')
            if not video or video.lower() == 'unknown':
                continue
            video_name = Path(video).name
            text = item.get('text', '')
            video_map.setdefault(video_name, []).append(text)

        sources = []
        for video, chunks in video_map.items():
            sources.append({
                'video': Path(video).stem,  # FE expects stem
                'chunks': chunks,
                'num_chunks': len(chunks)
            })

        return jsonify({'sources': sources})
    except Exception as e:
        return jsonify({'error': str(e), 'sources': []})


# -------------------------
# 🎥 Serve video
# -------------------------
@app.get('/videos/<name>')
def serve_video(name):
    return send_from_directory(VIDEOS_DIR, name)


# -------------------------
# 🔍 Query
# -------------------------
@app.post('/query')
def query():
    q = request.json.get('q') or request.json.get('question') or ''
    selected_sources = request.json.get('sources') or []

    if not q.strip():
        return jsonify({'error': 'Missing query'}), 400

    all_chunks = search_index(q)
    chunks_with_file = []

    try:
        with open('index/index.json', encoding='utf-8') as f:
            meta = json.load(f)
    except Exception as e:
        return jsonify({'error': 'No index metadata found', 'detail': str(e)}), 500

    # Normalize video names in meta
    meta_norm = {}
    for k, m in meta.items():
        video_raw = m.get('video', '').strip()
        video_name = Path(video_raw).name
        video_stem = unicodedata.normalize('NFKD', Path(video_name).stem).replace('\u00a0', ' ').lower()
        meta_norm[k] = {
            'text': m['text'],
            'video_stem': video_stem
        }

    # Normalize selected sources (stem)
    selected_norm = set()
    for s in selected_sources:
        try:
            selected_norm.add(
                unicodedata.normalize('NFKD', Path(s).stem).replace('\u00a0', ' ').lower()
            )
        except Exception as e:
            print("⚠️ Lỗi normalize source:", s, e)

    # Match chunks by exact text match returned by search_index
    for chunk in all_chunks:
        for k, m_norm in meta_norm.items():
            if m_norm['text'] == chunk:
                if not selected_sources or m_norm['video_stem'] in selected_norm:
                    chunks_with_file.append(f"[FILE: {m_norm['video_stem']}]\n{chunk}")
                break

    # Fallback: if no matches found, assemble from selected sources or all meta
    if not chunks_with_file:
        if selected_sources:
            for m_norm in meta_norm.values():
                if m_norm['video_stem'] in selected_norm: 
                    chunks_with_file.append(f"[FILE: {m_norm['video_stem']}]\n{m_norm['text']}")
        else:
            for m_norm in meta_norm.values():
                chunks_with_file.append(f"[FILE: {m_norm['video_stem']}]\n{m_norm['text']}")

    if not chunks_with_file:
        return jsonify({'answer': "Không tìm thấy dữ liệu phù hợp trong file đã chọn."})

    answer = summarize_results(q, chunks_with_file, model=SLM_MODEL)
    return jsonify({'answer': answer})

# -------------------------
# 📝 Summarize file
# -------------------------
@app.post('/summarize-file')
def summarize_file():
    file = request.files.get('file')
    if not file:
        return jsonify({'error': 'Missing file'}), 400

    save_path = os.path.join(INPUT_DIR, file.filename)
    file.save(save_path)

    text = extract_text(save_path)
    if not text.strip():
        return jsonify({'error': 'Cannot read file content'}), 400

    summary = summarize_whole_document(text)
    return jsonify({'summary': summary})


# -------------------------
# 🗑️ Delete source
# -------------------------
@app.post('/delete-source')
def delete_source():
    data = request.json or {}
    video_name = data.get('video', '')

    if not video_name:
        return jsonify({'error': 'Missing video name'}), 400

    # FE gửi stem -> normalize
    video_stem = unicodedata.normalize('NFKD', video_name.strip()).replace('\u00a0', ' ').replace('.mp4', '').lower()

    meta_path = Path('index/index.json')
    if not meta_path.exists():
        return jsonify({'error': 'No index metadata found'}), 404

    try:
        with open(meta_path, encoding='utf-8') as f:
            meta = json.load(f)

        # Tìm danh sách stored video names có stem khớp
        stored_names = set()
        for v in meta.values():
            stored_video = unicodedata.normalize('NFKD', v.get('video', '').strip()).replace('\u00a0', ' ')
            if Path(stored_video).stem.lower() == video_stem:
                stored_names.add(stored_video)

        if not stored_names:
            return jsonify({'message': 'No matching source found', 'removed': 0})

        removed_total = 0
        # Gọi delete_source_from_index cho từng stored name (faiss_utils sẽ rebuild index)
        for stored in stored_names:
            delete_source_from_index(stored)
            # count removed in meta by checking previous entries (best-effort)
            removed_total += sum(1 for v in meta.values() if Path(unicodedata.normalize('NFKD', v.get('video', '').strip()).replace('\u00a0',' ')).stem.lower() == video_stem)

        # Xóa file video vật lý (match by stem)
        for f in Path(VIDEOS_DIR).glob(f"{video_stem}*"):
            try:
                f.unlink()
            except Exception as e:
                print("⚠️ Could not delete video file:", f, e)

        return jsonify({'message': 'Deleted', 'removed': removed_total})

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': str(e)}), 500

# -------------------------
@app.post("/generate-mindmap")
def generate_mindmap():
    try:
        data = request.json or {}
        raw_sources = data.get("sources") or []
        if not isinstance(raw_sources, list):
            return jsonify({"error": "Sources phải là list"}), 400

        source_names: list[str] = []
        for item in raw_sources:
            candidate = None
            if isinstance(item, str):
                candidate = item.strip()
            elif isinstance(item, dict):
                for key in ("video", "name", "id", "source", "title"):
                    value = item.get(key)
                    if isinstance(value, str) and value.strip():
                        candidate = value.strip()
                        break
            if candidate:
                if candidate not in source_names:
                    source_names.append(candidate)

        if not source_names:
            return jsonify({"error": "No sources selected"}), 400

        strategy_requested = (
                    data.get("strategy") or data.get("mode") or data.get("method") or "iterative").strip().lower()

        if len(source_names) == 1:
            root_title = Path(source_names[0]).stem or "Mind Map"
        else:
            display_candidates = [Path(name).stem for name in source_names if Path(name).stem]
            if not display_candidates:
                root_title = "Mind Map tổng hợp"
            else:
                preview = ", ".join(display_candidates[:3])
                if len(display_candidates) > 3:
                    preview += f" + {len(display_candidates) - 3} nguồn"
                root_title = f"Tổng hợp: {preview}"

        with open("index/index.json", encoding="utf-8") as f:
            meta = json.load(f)

        # FIX 1: Normalize source linh hoạt hơn - loại timestamp và .mp4
        normalized_sources = set()
        for s in source_names:
            cleaned = unicodedata.normalize('NFKD', s.strip()).replace('\u00a0', ' ')
            cleaned = cleaned.replace('.mp4', '')
            # Loại timestamp dạng _YYYYMMDD_HHMMSS nếu có
            cleaned = re.sub(r'_\d{8}_\d{6}$', '', cleaned)
            cleaned = cleaned.strip().lower()
            normalized_sources.add(cleaned)

        # Lấy tất cả chunks từ meta
        all_chunks_with_meta = []
        for key, m in meta.items():
            video_raw = m.get("video", "").strip()
            if not video_raw:
                continue
            video_clean = unicodedata.normalize('NFKD', video_raw).replace('\u00a0', ' ')
            video_clean = video_clean.replace('.mp4', '')
            video_clean = re.sub(r'_\d{8}_\d{6}$', '', video_clean).strip().lower()

            if video_clean in normalized_sources:
                all_chunks_with_meta.append({
                    "text": m.get("text", ""),
                    "parent_id": m.get("parent_id"),
                    "sub_order": m.get("sub_order"),
                    "total_parts": m.get("total_parts"),
                    "is_subchunk": m.get("is_subchunk", False),
                    "key": key  # giữ key để debug nếu cần
                })

        if not all_chunks_with_meta:
            flat_nodes = [
                {"id": "root", "parent": None, "title": root_title},
                {"id": "root-0", "parent": "root", "title": "No content available"}
            ]
            strategy_used = "fallback"
        else:
            # FIX 2: Ghép sub-chunk thành chunk gốc trước khi đưa vào LLM
            merged_chunks = []
            sub_groups = {}
            normal_chunks = []

            for item in all_chunks_with_meta:
                if item["is_subchunk"]:
                    parent = item["parent_id"]
                    if parent not in sub_groups:
                        sub_groups[parent] = []
                    sub_groups[parent].append(item)
                else:
                    normal_chunks.append(item["text"])

            # Ghép sub-chunk theo parent
            for parent, subs in sub_groups.items():
                # Sort theo sub_order
                subs.sort(key=lambda x: x["sub_order"] or 0)
                merged_text = "\n\n".join(sub["text"] for sub in subs if sub["text"].strip())
                if merged_text.strip():
                    merged_chunks.append(merged_text)

            # Kết hợp chunk thường + chunk ghép
            final_chunks = normal_chunks + merged_chunks

            if not final_chunks:
                flat_nodes = [
                    {"id": "root", "parent": None, "title": root_title},
                    {"id": "root-0", "parent": "root", "title": "No content available"}
                ]
                strategy_used = "fallback"
            else:
                # Sinh mind map từ chunk gốc trọn vẹn
                if strategy_requested in {"cmgn", "semantic", "coreference"}:
                    try:
                        flat_nodes = generate_mindmap_cmgn(final_chunks, model=SLM_MODEL)
                        strategy_used = "cmgn"
                    except Exception as exc:
                        print(f"⚠️ CMGN failed: {exc}, fallback iterative")
                        flat_nodes = generate_mindmap_flat(final_chunks, model=SLM_MODEL)
                        strategy_used = "iterative"
                else:
                    flat_nodes = generate_mindmap_flat(final_chunks, model=SLM_MODEL)
                    strategy_used = "iterative"

        # Ép root_title
        if flat_nodes:
            root_node = next((n for n in flat_nodes if n.get("parent") is None), flat_nodes[0])
            root_node["title"] = root_title or root_node.get("title") or "Mind Map"

        mindmap_record = {
            "id": str(uuid.uuid4()),
            "title": root_title,
            "nodes": flat_nodes,
            "sources": source_names,
            "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "strategy": strategy_used,
        }

        _append_mindmap(mindmap_record)

        return jsonify(_mindmap_response(mindmap_record))

    except Exception as e:
        import traceback;
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
@app.get('/mindmaps')
def list_mindmaps():
    records = _load_mindmaps()
    return jsonify({"mindmaps": [_mindmap_response(r) for r in records]})


@app.delete('/mindmaps/<string:mindmap_id>')
def delete_mindmap(mindmap_id: str):
    records = _load_mindmaps()
    new_records = [r for r in records if r.get("id") != mindmap_id]
    if len(new_records) == len(records):
        return jsonify({"error": "Mind map not found"}), 404
    _save_mindmaps(new_records)
    return jsonify({"message": "Deleted"})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
