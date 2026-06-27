"""
Phân giải đường dẫn gốc một cách ỔN ĐỊNH theo vị trí file.

Vấn đề: nhiều module tính DATA_DIR mặc định bằng Path(__file__).parent. Khi tái cấu
trúc thư mục (file chui sâu vào app/domains/...), __file__.parent đổi -> data path
mặc định lệch. paths.py neo mọi thứ vào BE_ROOT (thư mục chứa shared/, app/, services/)
nên mặc định không đổi dù file nằm ở đâu.

BE_ROOT = thư mục BE (vì file này luôn ở BE/shared/paths.py).
"""

from __future__ import annotations

import os
from pathlib import Path

# shared/paths.py -> parent = BE/shared, parent.parent = BE
BE_ROOT = Path(__file__).resolve().parent.parent


def default_data_dir() -> Path:
    """DATA_DIR: lấy từ env nếu có, mặc định = BE_ROOT (giữ nguyên hành vi cũ)."""
    return Path(os.environ.get("DATA_DIR", str(BE_ROOT)))
