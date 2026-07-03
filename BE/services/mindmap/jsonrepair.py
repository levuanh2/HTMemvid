"""JSON repair string-aware dùng chung cho mindmap pipeline (cũ + mới)."""
from __future__ import annotations

import re


def repair_json_text(raw: str) -> str:
    """Sửa nhẹ JSON gần đúng từ LLM: bỏ code fence, trích khối {...} CÂN BẰNG đầu
    tiên (bỏ rác trước/sau), bỏ dấu phẩy thừa trước '}'/']'. Chuỗi trả về vẫn có
    thể lỗi — caller bọc try và rơi xuống fallback deterministic nếu cần."""
    s = (raw or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\s*```\s*$", "", s).strip()
    start = s.find("{")
    if start != -1:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(s)):
            c = s[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            else:
                if c == '"':
                    in_str = True
                elif c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        s = s[start:i + 1]
                        break
    # bỏ dấu phẩy thừa trước '}'/']' — CHỈ ngoài chuỗi (tránh hỏng comma trong string).
    out_chars: list = []
    in_str = False
    esc = False
    n = len(s)
    for i, c in enumerate(s):
        if in_str:
            out_chars.append(c)
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
            out_chars.append(c)
            continue
        if c == ",":
            j = i + 1
            while j < n and s[j] in " \t\r\n":
                j += 1
            if j < n and s[j] in "}]":
                continue  # bỏ dấu phẩy thừa
        out_chars.append(c)
    return "".join(out_chars).strip()
