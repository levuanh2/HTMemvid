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
    # chỉ .avi "đọc được" → buộc fallthrough qua mp4v/avc1 (.mp4) sang .avi
    monkeypatch.setattr(vu, "_video_is_valid", lambda p: p.endswith(".avi"))

    out = vu.save_qr_frames_to_video(_frames(), prefix="doc x")
    assert out.endswith(".avi"), "phải bỏ codec cho file hỏng, chọn codec cho file đọc được"


def test_raises_when_no_codec_produces_valid_file(tmp_path, monkeypatch):
    monkeypatch.setattr(vu, "VIDEOS_DIR", str(tmp_path))
    monkeypatch.setattr(vu.cv2, "VideoWriter_fourcc", lambda *cs: "".join(cs))
    monkeypatch.setattr(vu.cv2, "VideoWriter", _FakeWriter)
    monkeypatch.setattr(vu, "_video_is_valid", lambda p: False)

    with pytest.raises(RuntimeError):
        vu.save_qr_frames_to_video(_frames(), prefix="doc")
