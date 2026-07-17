# BE/tests/test_summary_routes.py — mirror test_mindmap_routes.py
import pytest


def test_generate_cache_hit_returns_done_without_job_id(client, monkeypatch):
    import app.main as be_main
    from app.domains.summary import store
    rec = {"id": "s1", "schema_version": 2, "title": "T", "sources": ["a_docx"],
           "content_hash": "h" * 64, "created_at": "2026-07-06T00:00:00Z",
           "length_mode": "medium", "overview": "ov", "sections": [], "entities": [],
           "generator": {"degraded": False, "missing": []}}
    monkeypatch.setattr(be_main, "_summary_input_and_hash",
                        lambda sources, length_mode, mode: ({"chunks": [1]}, "h" * 64))
    monkeypatch.setattr(store, "get_by_hash", lambda h: rec if h == "h" * 64 else None)
    r = client.post("/generate-summary", json={"sources": ["a_docx"]})
    assert r.status_code == 200
    body = r.get_json()
    assert body["status"] == "done" and body["cached"] is True
    assert body["result"]["id"] == "s1"
    assert "job_id" not in body  # contract cache-hit (aec6017): FE branch theo status


def test_generate_force_bypasses_cache_starts_job(client, monkeypatch):
    import app.main as be_main
    from app.domains.summary import store
    monkeypatch.setattr(be_main, "_summary_input_and_hash",
                        lambda sources, length_mode, mode: ({"chunks": [1]}, "h" * 64))
    monkeypatch.setattr(store, "get_by_hash", lambda h: {"id": "s1"})
    started = {}

    def fake_start(sources, mm, h, length_mode, mode):
        started["length_mode"] = length_mode
        started["mode"] = mode
        return "jid"

    monkeypatch.setattr(be_main, "_start_summary_job", fake_start)
    r = client.post("/generate-summary", json={"sources": ["a_docx"], "force": True,
                                               "length_mode": "detailed"})
    assert r.status_code == 202
    assert r.get_json()["job_id"] == "jid"
    assert started["length_mode"] == "detailed"
    assert started["mode"] == "standard"   # không gửi mode → default standard


def test_invalid_length_mode_defaults_medium(client, monkeypatch):
    import app.main as be_main
    seen = {}

    def capture(sources, length_mode, mode):
        seen["length_mode"] = length_mode
        return {"chunks": [1]}, "h" * 64

    monkeypatch.setattr(be_main, "_summary_input_and_hash", capture)
    monkeypatch.setattr(be_main, "_start_summary_job", lambda *a: "jid")
    from app.domains.summary import store
    monkeypatch.setattr(store, "get_by_hash", lambda h: None)
    r = client.post("/generate-summary", json={"sources": ["a_docx"], "length_mode": "bogus"})
    assert r.status_code == 202
    assert seen["length_mode"] == "medium"


def test_mode_defaults_standard_when_missing(client, monkeypatch):
    import app.main as be_main
    seen = {}

    def capture(sources, length_mode, mode):
        seen["mode"] = mode
        return {"chunks": [1]}, "h" * 64

    monkeypatch.setattr(be_main, "_summary_input_and_hash", capture)
    monkeypatch.setattr(be_main, "_start_summary_job", lambda *a: "jid")
    from app.domains.summary import store
    monkeypatch.setattr(store, "get_by_hash", lambda h: None)
    r = client.post("/generate-summary", json={"sources": ["a_docx"]})
    assert r.status_code == 202
    assert seen["mode"] == "standard"


def test_study_mode_threaded_to_input_and_job(client, monkeypatch):
    import app.main as be_main
    started = {}
    monkeypatch.setattr(be_main, "_summary_input_and_hash",
                        lambda sources, length_mode, mode: ({"chunks": [1]}, "h" * 64))

    def fake_start(sources, mm, h, length_mode, mode):
        started["mode"] = mode
        return "jid"

    monkeypatch.setattr(be_main, "_start_summary_job", fake_start)
    from app.domains.summary import store
    monkeypatch.setattr(store, "get_by_hash", lambda h: None)
    r = client.post("/generate-summary", json={"sources": ["a_docx"], "mode": "study"})
    assert r.status_code == 202
    assert started["mode"] == "study"


def test_invalid_mode_returns_400(client):
    r = client.post("/generate-summary", json={"sources": ["a_docx"], "mode": "bogus"})
    assert r.status_code == 400


def test_generate_no_sources_400(client):
    assert client.post("/generate-summary", json={"sources": []}).status_code == 400


def test_status_endpoint_shapes(client, monkeypatch):
    from app.domains.jobs import jobs_store as js
    monkeypatch.setattr(js, "get_job", lambda jid: {
        "job_type": "summary", "status": "running", "progress": 40,
        "current_node": "SummarizeSections", "result": None, "error": None,
    } if jid == "sj" else None)
    body = client.get("/summary-status/sj").get_json()
    assert body["status"] == "running" and body["current_node"] == "SummarizeSections"
    assert client.get("/summary-status/nope").status_code == 404


def test_status_rejects_other_job_type(client, monkeypatch):
    from app.domains.jobs import jobs_store as js
    monkeypatch.setattr(js, "get_job", lambda jid: {"job_type": "mindmap", "status": "running"})
    assert client.get("/summary-status/x").status_code == 404


def test_cancel_endpoint_sets_flag(client, monkeypatch):
    # Cancel giờ 404 job lạ (cùng contract /summary-status) → job phải tồn tại.
    monkeypatch.setenv("AUTH_PROTECT_APP_APIS", "0")
    from app.domains.jobs import jobs_store as js
    called = {}
    monkeypatch.setattr(js, "get_job", lambda jid: {"job_type": "summary", "status": "running"})
    monkeypatch.setattr(js, "request_cancel", lambda jid: called.setdefault("jid", jid))
    r = client.post("/summary-cancel/abc")
    assert r.status_code == 200 and called["jid"] == "abc"
    assert r.get_json()["status"] == "running"  # running → cooperative, trả status hiện tại


def test_cancel_unknown_job_returns_404(client, monkeypatch):
    monkeypatch.setenv("AUTH_PROTECT_APP_APIS", "0")
    from app.domains.jobs import jobs_store as js
    monkeypatch.setattr(js, "get_job", lambda jid: None)
    assert client.post("/summary-cancel/nope").status_code == 404


def test_cancel_rejects_other_job_type(client, monkeypatch):
    monkeypatch.setenv("AUTH_PROTECT_APP_APIS", "0")
    from app.domains.jobs import jobs_store as js
    monkeypatch.setattr(js, "get_job", lambda jid: {"job_type": "mindmap", "status": "running"})
    assert client.post("/summary-cancel/x").status_code == 404


def test_cancel_interrupted_job_returns_cancelled(client, monkeypatch, tmp_path):
    # Job mồ côi sau restart: cancel phải trả terminal "cancelled" NGAY —
    # regression cho UI kẹt "Đang huỷ… (36%)" (không executor nào ack cờ).
    monkeypatch.setenv("AUTH_PROTECT_APP_APIS", "0")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from app.domains.jobs import jobs_store as js
    js.create_job("sjint", job_type="summary", status="running", progress=36)
    js.mark_interrupted_jobs()
    r = client.post("/summary-cancel/sjint")
    assert r.status_code == 200
    assert r.get_json()["status"] == "cancelled"
    assert js.get_job("sjint")["status"] == "cancelled"


def test_cancel_done_job_is_safe_noop(client, monkeypatch, tmp_path):
    # Idempotent: cancel job đã xong không phá status/result.
    monkeypatch.setenv("AUTH_PROTECT_APP_APIS", "0")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from app.domains.jobs import jobs_store as js
    js.create_job("sjdone", job_type="summary", status="running")
    js.update_job("sjdone", status="done", progress=100, result={"id": "r1"})
    r = client.post("/summary-cancel/sjdone")
    assert r.status_code == 200
    assert r.get_json()["status"] == "done"
    assert js.get_job("sjdone")["result"] == {"id": "r1"}


def test_list_and_delete_use_store(client, monkeypatch):
    from app.domains.summary import store
    monkeypatch.setattr(store, "list_records", lambda: [{"id": "x"}])
    monkeypatch.setattr(store, "delete_record", lambda sid: sid == "x")
    assert client.get("/summaries").get_json()["summaries"] == [{"id": "x"}]
    assert client.delete("/summaries/x").status_code == 200
    assert client.delete("/summaries/nope").status_code == 404


def test_old_sync_endpoints_removed(client):
    # Pipeline cũ đã xóa hẳn — không giữ song song
    assert client.post("/summarize-documents", json={"sources": ["a"]}).status_code == 404
    assert client.post("/summarize-file").status_code == 404
    assert client.post("/summaries", json={}).status_code == 405  # chỉ còn GET/OPTIONS
