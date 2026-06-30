"""save_qr_frames_to_video phải CHỌN codec bằng cách kiểm tra file ghi ra ĐỌC ĐƯỢC,
không tin writer.isOpened()/write() (headless: open OK nhưng không encode → 0 frame)."""
import numpy as np
import pytest

import app.domains.ingest.video_utils as vu


class _FakeWriter:
    def __init__(self, path, fourcc, fps, size, isColor=True):
        self.path = path

    def isOpened(self):
        return True

    def write(self, frame):
        return None  # giống cv2: write trả None → KHÔNG được dùng làm tín hiệu thành công

    def release(self):
        pass


def _frames():
    return [np.zeros((8, 8, 3), np.uint8) for _ in range(3)]


def test_picks_codec_whose_file_validates_and_falls_through(tmp_path, monkeypatch):
    monkeypatch.setattr(vu, "VIDEOS_DIR", str(tmp_path))
    monkeypatch.setattr(vu.cv2, "VideoWriter_fourcc", lambda *cs: "".join(cs))
    monkeypatch.setattr(vu.cv2, "VideoWriter", _FakeWriter)
    
    calls = 0
    def mock_valid(p):
        nonlocal calls
        calls += 1
        return calls > 1 and p.endswith(".mp4")
    monkeypatch.setattr(vu, "_video_is_valid", mock_valid)

    out = vu.save_qr_frames_to_video(_frames(), prefix="doc x")
    assert out.endswith(".mp4"), "phải bỏ codec cho file hỏng, chọn codec cho file đọc được"
    assert calls == 2, "phải fallthrough qua candidate thứ hai"



def test_raises_when_no_codec_produces_valid_file(tmp_path, monkeypatch):
    monkeypatch.setattr(vu, "VIDEOS_DIR", str(tmp_path))
    monkeypatch.setattr(vu.cv2, "VideoWriter_fourcc", lambda *cs: "".join(cs))
    monkeypatch.setattr(vu.cv2, "VideoWriter", _FakeWriter)
    monkeypatch.setattr(vu, "_video_is_valid", lambda p: False)

    with pytest.raises(RuntimeError):
        vu.save_qr_frames_to_video(_frames(), prefix="doc")


def test_decode_video_qr_preserves_order(monkeypatch):
    import hashlib
    import app.domains.ingest.video_utils as vu

    c1 = hashlib.sha256(b"alpha").hexdigest()[:16]
    c2 = hashlib.sha256(b"beta").hexdigest()[:16]

    class _Cap:
        def __init__(self, path):
            self._i = 0
        def read(self):
            frames = [
                f"[METADATA:parent=0,order=1,video=d.mp4,ts=t,checksum={c1}] alpha",
                f"[METADATA:parent=1,order=1,video=d.mp4,ts=t,checksum={c2}] beta"
            ]
            if self._i >= len(frames):
                return False, None
            f = frames[self._i]
            self._i += 1
            return True, f
        def release(self):
            pass

    class _Det:
        def detectAndDecode(self, frame):
            return frame, None, None

    monkeypatch.setattr(vu.cv2, "VideoCapture", _Cap)
    monkeypatch.setattr(vu.cv2, "QRCodeDetector", _Det)

    out = vu.decode_video_qr("d.mp4")
    assert [fi for fi, _ in out] == [0, 1], "phải giữ thứ tự frame"
    assert [t for _, t in out] == ["alpha", "beta"]


def test_decode_frame(monkeypatch):
    import app.domains.ingest.video_utils as vu

    class _Cap:
        def __init__(self, path):
            self.pos = 0
        def set(self, prop, val):
            self.pos = val
        def read(self):
            if self.pos == 2:
                return True, "[METADATA:parent=0,order=1,video=d.mp4,ts=t,checksum=xyz] hello frame"
            return False, None
        def release(self):
            pass

    class _Det:
        def detectAndDecode(self, frame):
            return frame, None, None

    monkeypatch.setattr(vu.cv2, "VideoCapture", _Cap)
    monkeypatch.setattr(vu.cv2, "QRCodeDetector", _Det)

    out = vu.decode_frame("d.mp4", 2)
    assert out == "hello frame"


