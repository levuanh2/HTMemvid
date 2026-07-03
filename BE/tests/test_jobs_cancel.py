from app.domains.jobs import jobs_store as js

def test_cancel_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    js.create_job("j1", job_type="mindmap")
    assert js.is_cancel_requested("j1") is False
    js.request_cancel("j1")
    assert js.is_cancel_requested("j1") is True
    assert js.get_job("j1")["cancel_requested"] is True

def test_cancel_unknown_job_safe(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    js.request_cancel("khong_ton_tai")
    assert js.is_cancel_requested("khong_ton_tai") is False
