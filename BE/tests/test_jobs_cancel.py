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


def test_cancel_running_job_stays_cooperative(tmp_path, monkeypatch):
    # executor sống → chỉ set cờ, executor tự chuyển terminal ở checkpoint kế
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    js.create_job("jr", job_type="summary", status="running", progress=36)
    js.request_cancel("jr")
    j = js.get_job("jr")
    assert j["status"] == "running" and j["cancel_requested"] is True
    assert j["progress"] == 36


def test_cancel_pending_job_cancels_immediately(tmp_path, monkeypatch):
    # queued, chưa executor nào nhận → không ai ack cờ → phải terminal ngay
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    js.create_job("jp", job_type="summary", status="pending")
    js.request_cancel("jp")
    assert js.get_job("jp")["status"] == "cancelled"


def test_cancel_interrupted_job_cancels_immediately(tmp_path, monkeypatch):
    # job mồ côi sau BE restart (mark_interrupted_jobs) — root cause UI kẹt "Đang huỷ…"
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    js.create_job("ji", job_type="summary", status="running", progress=36)
    js.mark_interrupted_jobs()
    assert js.get_job("ji")["status"] == "interrupted"
    js.request_cancel("ji")
    j = js.get_job("ji")
    assert j["status"] == "cancelled" and j["cancel_requested"] is True


def test_cancel_terminal_job_is_idempotent_noop(tmp_path, monkeypatch):
    # cancel job đã done KHÔNG được đụng status/result; gọi lặp lại vẫn an toàn
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    js.create_job("jd", job_type="summary", status="running")
    js.update_job("jd", status="done", progress=100, result={"id": "r1"})
    js.request_cancel("jd")
    js.request_cancel("jd")
    j = js.get_job("jd")
    assert j["status"] == "done" and j["result"] == {"id": "r1"}
    js.create_job("jc", job_type="summary", status="pending")
    js.request_cancel("jc")
    js.request_cancel("jc")  # cancel lần 2 trên job đã cancelled → giữ nguyên
    assert js.get_job("jc")["status"] == "cancelled"
