from app.domains.ingest.clean import clean_markdown, promote_headings


def test_bold_standalone_line_promoted():
    md = "Mở đầu.\n\n**Phát hiện xâm phạm an ninh**\n\nNội dung đoạn."
    out = promote_headings(md)
    assert "## Phát hiện xâm phạm an ninh" in out


def test_numbered_depths():
    md = "\n\n".join(["Chương 1 Giới thiệu", "1. Tổng quan", "1.1 Chi tiết", "I. Phụ lục"])
    out = promote_headings(md)
    assert "# Chương 1 Giới thiệu" in out
    assert "## 1. Tổng quan" in out
    assert "### 1.1 Chi tiết" in out
    assert "# I. Phụ lục" in out


def test_ordered_list_items_not_promoted():
    # item list nằm sát nhau (không blank 2 phía) — không được biến thành heading
    md = "Danh sách:\n1. mua trứng\n2. mua sữa\n3. mua bánh mì"
    assert promote_headings(md) == md


def test_sentence_and_long_line_not_promoted():
    md = "**Đây là một câu văn bold kết thúc bằng dấu chấm.**\n\n" \
         "1. " + "x" * 100
    assert "##" not in promote_headings(md)


def test_noop_when_headings_already_exist():
    md = "# Tiêu đề thật\n\n**Bold thường**\n\n1. Mục"
    assert promote_headings(md) == md


def test_clean_markdown_applies_promotion():
    out = clean_markdown("Đoạn mở.\n\n**Quy định camera giám sát**\n\nNội dung.")
    assert "## Quy định camera giám sát" in out


# ── mammoth dialect: __bold__ + backslash-escape (regression của fix 2026-07-05) ──

def test_mammoth_double_underscore_bold_promoted():
    md = "Mở đầu.\n\n__Phát hiện xâm phạm an ninh__\n\nNội dung."
    out = promote_headings(md)
    assert "## Phát hiện xâm phạm an ninh" in out


def test_mammoth_escaped_numbered_bold_line_promoted():
    # mammoth thật sinh: __1\. Làm thế nào ...__ (số bị escape) — phải ra ### /##
    md = "Intro.\n\n__1\. Làm thế nào để phát hiện xâm phạm?__\n\nTrả lời dài."
    out = clean_markdown(md)
    assert "## 1. Làm thế nào để phát hiện xâm phạm?" in out


def test_unescape_conservative():
    from app.domains.ingest.clean import unescape_mammoth
    assert unescape_mammoth("Phân tích \(Behavioral analysis\)\.") == "Phân tích (Behavioral analysis)."
    assert unescape_mammoth("giá \, xong\!") == "giá , xong!"
    # KHÔNG unescape ký tự cấu trúc markdown — tránh tạo heading/list/link giả
    assert unescape_mammoth("\# not heading \* not list \- dash \[x\]") == \
        "\# not heading \* not list \- dash \[x\]"


def test_clean_markdown_unescapes_before_promotion():
    out = clean_markdown("Đoạn.\n\n__2\. Quy định camera giám sát\?__\n\nNội dung.")
    assert "## 2. Quy định camera giám sát?" in out
    assert "\." not in out.split("\n")[0]


def test_numbered_title_starting_with_digit_promoted():
    # codex #4: "1. 2024 Kết quả" — title mở đầu bằng số vẫn là heading
    md = "Intro.\n\n1. 2024 Kết quả nghiên cứu\n\nNội dung."
    assert "## 1. 2024 Kết quả nghiên cứu" in promote_headings(md)


def test_long_bold_question_line_promoted():
    # Q&A thật: câu hỏi bold dài — bold đứng một mình là tín hiệu mạnh, cap dài
    # (250; đo thật: câu hỏi Q4 doc mẫu = 203 ký tự) so với dòng đánh số trần (90)
    q = ("1. Làm thế nào để các cơ quan chức năng phát hiện và xử lý các hành vi "
         "xâm phạm an ninh mạng, đặc biệt khi tội phạm sử dụng công nghệ ẩn danh "
         "như VPN hay mã hóa? Và cần bổ sung các biện pháp giám sát, ngăn chặn "
         "ra sao cho hiệu quả?")
    assert 200 < len(q) <= 250
    md = f"Intro.\n\n__{q}__\n\nTrả lời."
    out = promote_headings(md)
    assert f"## {q}" in out


def test_long_plain_numbered_line_not_promoted():
    # dòng đánh số KHÔNG bold dài >90 ký tự = đoạn văn, không phải heading
    line = "1. " + "một đoạn văn khá dài " * 6
    md = f"Intro.\n\n{line.strip()}\n\nTiếp."
    assert "## 1." not in promote_headings(md)
