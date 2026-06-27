from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROTO_DIR = ROOT / "shared" / "proto"
GEN_DIR = PROTO_DIR / "gen"
PROTO_FILES = [
    PROTO_DIR / "common.proto",
    PROTO_DIR / "llm.proto",
    PROTO_DIR / "mindmap.proto",
]
IMPORT_RE = re.compile(r"^import (\w+_pb2) as (\w+)$", re.MULTILINE)


def rewrite_generated_imports() -> None:
    for path in GEN_DIR.glob("*_pb2*.py"):
        content = path.read_text(encoding="utf-8")
        updated = IMPORT_RE.sub(r"from . import \1 as \2", content)
        if updated != content:
            path.write_text(updated, encoding="utf-8")


def main() -> None:
    GEN_DIR.mkdir(parents=True, exist_ok=True)
    (GEN_DIR / "__init__.py").touch(exist_ok=True)

    cmd = [
        sys.executable,
        "-m",
        "grpc_tools.protoc",
        "-I",
        str(PROTO_DIR),
        f"--python_out={GEN_DIR}",
        f"--grpc_python_out={GEN_DIR}",
        *(str(path) for path in PROTO_FILES),
    ]
    subprocess.run(cmd, check=True, cwd=ROOT)
    rewrite_generated_imports()


if __name__ == "__main__":
    main()
