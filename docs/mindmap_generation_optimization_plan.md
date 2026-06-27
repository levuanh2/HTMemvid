# Kế hoạch tối ưu Mindmap Generation

## 1. Mục tiêu

Tối ưu pipeline sinh mindmap để:

- Sinh nhanh hơn nhưng vẫn đủ ý.
- Giảm số lần gọi LLM không cần thiết.
- Giữ nguyên API cũ để không phá frontend.
- Giữ được chất lượng mindmap ở mức tốt cho UI hiện tại.
- Cho phép người dùng chọn mức sinh: `fast`, `balanced`, `quality`.

Pipeline hiện tại đã có adaptive strategy selection theo kích thước dữ liệu, gồm `single_call_schema`, `mindmap_v2`, `cmgn`, `iterative`, `multilevel`. Tuy nhiên, phần tốn thời gian nhất là số lượng LLM calls, đặc biệt ở `cmgn`, `iterative`, `multilevel`, và bước tạo Visual Diagram bằng LLM sau khi đã có flat nodes.

---

## 2. Hiện trạng pipeline

### 2.1 Luồng hiện tại

```txt
Load chunks từ index.json
→ Merge sub-chunks
→ Chọn strategy
→ Check cache
→ Sinh flat_nodes
→ Gọi LLM tạo Visual Diagram
→ Lưu mindmaps.json
→ Update cache
```

### 2.2 Thời gian theo strategy hiện tại

| Strategy | LLM calls | Thời gian ước tính |
|---|---:|---:|
| `single_call_schema` | 1-3 | 6-20s |
| `mindmap_v2` | 1 | 7-18s |
| `cmgn` | 5 | 35-70s |
| `iterative` | 2 + N*2 + 3 | 35-100s |
| `multilevel` | 3-5 | 40-75s |
| Visual Diagram LLM | +1 | +10-20s |

### 2.3 Vấn đề chính

1. `Visual Diagram` đang gọi LLM riêng sau khi đã có `flat_nodes`, làm tăng thêm khoảng 10-20 giây.
2. `cmgn` có 5 LLM calls vì có thêm 3 critics.
3. `iterative` có thể rất chậm vì mở rộng node theo BFS.
4. Fallback chain có thể rơi vào strategy nặng dù người dùng chỉ cần sinh nhanh.
5. Cache chưa phân biệt rõ generation mode và visual diagram mode.

---

## 3. Chiến lược tối ưu tổng thể

Thêm 3 chế độ sinh:

| Mode | Mục tiêu | Dùng khi nào |
|---|---|---|
| `fast` | Sinh nhanh nhất, đủ ý chính | Preview, tài liệu nhiều, demo nhanh |
| `balanced` | Cân bằng tốc độ và chất lượng | Default |
| `quality` | Chất lượng cao nhất | Khi user cần mindmap sâu, có kiểm chứng |

---

## 4. Hành vi mới theo mode

### 4.1 Fast mode

```txt
- Không gọi LLM visual diagram.
- Không chạy critics.
- Không dùng cmgn / iterative mặc định.
- Ưu tiên single_call_schema hoặc mindmap_v2.
- Giới hạn output khoảng 35 nodes.
- Max depth: 2.
```

Chiến lược:

```txt
< 12k chars        → single_call_schema
12k - 60k chars    → mindmap_v2
> 60k chars        → mindmap_v2 với context compression
fallback           → deterministic main branches
```

### 4.2 Balanced mode

```txt
- Default mode.
- Không gọi LLM visual diagram nếu deterministic diagram đủ tốt.
- Có thể dùng cmgn_light cho tài liệu lớn.
- cmgn_light bỏ 3 critics.
- Giới hạn output khoảng 55 nodes.
- Max depth: 3.
```

Chiến lược:

```txt
< 8k chars                  → single_call_schema
≤ 25 chunks, < 35k chars     → mindmap_v2
≤ 60 chunks, < 80k chars     → cmgn_light
> 80k chars                  → multilevel_fast
fallback                     → single_call_schema → deterministic main branches
```

### 4.3 Quality mode

```txt
- Giữ workflow chất lượng cao.
- Cho phép cmgn full với critics.
- Cho phép iterative nếu tài liệu rất lớn.
- Gọi LLM visual diagram để refine diagram.
- Giới hạn output khoảng 90 nodes.
- Max depth: 4.
```

Chiến lược:

```txt
Giữ logic hiện tại:
single_call_schema → mindmap_v2 → cmgn → iterative → multilevel
```

---

## 5. Các thay đổi kỹ thuật cần triển khai

## Phase 1 — Thêm generation mode

### Việc cần làm

- Đọc `mode` từ request body hoặc env.
- Default là `balanced`.
- Response trả thêm `mode`.
- Không phá request cũ.

### API đề xuất

Request cũ vẫn hoạt động:

```json
{
  "sources": ["file.pdf"]
}
```

Request mới:

```json
{
  "sources": ["file.pdf"],
  "mode": "balanced"
}
```

Response:

```json
{
  "id": "...",
  "title": "...",
  "nodes": [],
  "diagram": {},
  "strategy": "mindmap_v2",
  "mode": "balanced"
}
```

---

## Phase 2 — Sửa strategy selection theo mode

### Hàm cần sửa

```python
def select_mindmap_strategy(chunks, force_strategy=None, mode="balanced"):
    ...
```

### Logic mới

```python
def select_mindmap_strategy(chunks, force_strategy=None, mode="balanced"):
    if force_strategy:
        return force_strategy

    n_chunks = len(chunks)
    total_chars = sum(len(c.get("text", "") if isinstance(c, dict) else str(c)) for c in chunks)

    if mode == "fast":
        if total_chars < 12000:
            return "single_call_schema"
        return "mindmap_v2"

    if mode == "balanced":
        if total_chars < 8000:
            return "single_call_schema"
        if n_chunks <= 25 and total_chars < 35000:
            return "mindmap_v2"
        if n_chunks <= 60 and total_chars < 80000:
            return "cmgn_light"
        return "multilevel_fast"

    # quality
    if total_chars < 2500:
        return "single_call_schema"
    if n_chunks <= 4 and total_chars < 8000:
        return "single_call_schema"
    if n_chunks <= 18 and total_chars < 18000:
        return "mindmap_v2"
    if n_chunks <= 45 and total_chars < 50000:
        return "cmgn"
    return "iterative"
```

---

## Phase 3 — Thêm `cmgn_light`

### Mục tiêu

Giảm `cmgn` từ 5 LLM calls xuống 2 LLM calls.

### Cách làm

Nếu hiện tại có function:

```python
generate_mindmap_cmgn(chunks, model=MINDMAP_MODEL)
```

Thêm tham số:

```python
generate_mindmap_cmgn(chunks, model=MINDMAP_MODEL, enable_critics=True)
```

Behavior:

```txt
enable_critics=True:
- coreference graph
- mindmap generation
- factuality critic
- local critic
- global critic

// quality mode

enable_critics=False:
- coreference graph
- mindmap generation
- deterministic sanitize/postprocess

// balanced mode
```

Nếu không muốn sửa function cũ, tạo wrapper:

```python
def generate_mindmap_cmgn_light(chunks, model):
    return generate_mindmap_cmgn(chunks, model=model, enable_critics=False)
```

---

## Phase 4 — Visual Diagram deterministic-first

### Vấn đề

Hiện tại pipeline gọi LLM để tạo Visual Diagram sau khi đã có flat nodes. Đây là chi phí lớn nhưng không phải lúc nào cũng cần.

### Mục tiêu

Đổi từ:

```txt
flat_nodes
→ Visual Diagram LLM
→ fallback deterministic nếu fail
```

thành:

```txt
flat_nodes
→ deterministic diagram trước
→ chỉ gọi LLM refine nếu mode=quality hoặc diagram quá nghèo
```

### Hàm mới

```python
def build_visual_diagram_by_mode(flat_nodes, final_chunks, root_title, sources, mode):
    if mode == "fast":
        return _flat_nodes_to_visual_diagram(flat_nodes, root_title=root_title, sources=sources)

    if mode == "balanced":
        diagram = _flat_nodes_to_visual_diagram(flat_nodes, root_title=root_title, sources=sources)
        if len(flat_nodes) < 8 or _diagram_quality_low(diagram):
            return _build_visual_diagram_llm(flat_nodes, final_chunks, root_title, sources)
        return diagram

    return _build_visual_diagram_llm(flat_nodes, final_chunks, root_title, sources)
```

### Ghi chú

- Rename hàm LLM cũ từ `_build_visual_diagram` thành `_build_visual_diagram_llm`.
- `_build_visual_diagram` mới nên là wrapper gọi `build_visual_diagram_by_mode`.

---

## Phase 5 — Cap nodes để đủ ý nhưng không rối

### Mục tiêu

Giữ mindmap đủ ý nhưng không sinh quá nhiều node làm chậm backend và rối frontend.

### Hàm mới

```python
def cap_mindmap_nodes(flat_nodes, mode):
    if mode == "fast":
        return prune_tree(flat_nodes, max_total=35, max_depth=2, max_children_per_node=6)
    if mode == "balanced":
        return prune_tree(flat_nodes, max_total=55, max_depth=3, max_children_per_node=7)
    return prune_tree(flat_nodes, max_total=90, max_depth=4, max_children_per_node=10)
```

### Rule prune

- Không cắt root.
- Ưu tiên giữ level 1 và level 2.
- Nếu cắt node con, có thể thêm `hidden_count` vào parent.
- Không để parent trỏ tới node đã bị xóa.
- Loại duplicate title cùng parent.

---

## Phase 6 — Context compression

### Mục tiêu

Giảm prompt length để LLM trả nhanh hơn.

### Cách làm

Trước khi gọi LLM:

```txt
cluster chunks
→ lấy top keywords bằng TF-IDF
→ lấy 1-2 representative sentences
→ tạo cluster summaries ngắn
→ gửi LLM outline thay vì full text dài
```

### Giới hạn context theo mode

| Mode | Max context chars |
|---|---:|
| `fast` | 12,000 |
| `balanced` | 20,000 |
| `quality` | 40,000 |

---

## Phase 7 — Cache theo mode

### Mục tiêu

Không dùng nhầm cache giữa các mode, nhưng vẫn tái sử dụng được khi hợp lý.

### Content hash nên bao gồm

```txt
sources
n_chunks
total_chars
chunks_hash
strategy
generation_mode
MINDMAP_MODEL
schema_version
EMBEDDING_MODEL_NAME
embedding_dim
visual_diagram_mode
```

### Tối ưu reuse

- Nếu đã có cache `quality`, có thể prune lại để phục vụ `balanced`/`fast`.
- Diagram deterministic có thể rebuild nhanh, không cần cache LLM diagram nếu không gọi LLM.

---

## Phase 8 — Fallback chain theo mode

### Fast fallback

```txt
mindmap_v2
→ single_call_schema
→ deterministic main branches
```

### Balanced fallback

```txt
mindmap_v2
→ cmgn_light
→ multilevel_fast
→ single_call_schema
→ deterministic main branches
```

### Quality fallback

```txt
Giữ chain hiện tại:
primary
→ multilevel
→ iterative
→ single_call
→ fallback branches
```

---

## Phase 9 — Timing logs

### Mục tiêu

Biết chính xác chậm ở đâu.

### Log đề xuất

```txt
[MindMap Timing] mode=balanced strategy=mindmap_v2 nodes=42 total=18.2s load=0.1s merge=0.2s mindmap=14.5s visual=0.1s cache=0.0s
```

### Các mốc cần đo

```txt
time_load_chunks
time_merge_subchunks
time_cache_lookup
time_strategy_select
time_mindmap_generation
time_visual_diagram
time_save
time_total
```

---

## 6. Acceptance Criteria

### Case 1 — tài liệu nhỏ

```txt
Input: 3 chunks, 5k chars
Mode: fast
Expected:
- strategy = single_call_schema
- visual diagram = deterministic
- total < 15s
```

### Case 2 — tài liệu trung bình

```txt
Input: 12 chunks, 15k chars
Mode: balanced
Expected:
- strategy = mindmap_v2
- visual diagram = deterministic
- total < 20s
```

### Case 3 — tài liệu lớn

```txt
Input: 30 chunks, 40k chars
Mode: balanced
Expected:
- strategy = cmgn_light hoặc mindmap_v2
- no critics
- no visual LLM
- total giảm rõ so với cmgn full
```

### Case 4 — quality mode

```txt
Mode: quality
Expected:
- vẫn có thể chạy cmgn full / iterative
- visual diagram LLM enabled
- chất lượng cao nhất
```

---

## 7. Rủi ro và cách giảm rủi ro

| Rủi ro | Cách xử lý |
|---|---|
| Fast mode thiếu ý | Giữ 5-7 nhánh chính, mỗi nhánh 3-5 ý |
| Cache miss nhiều | Thêm mode vào hash, nhưng cho phép reuse quality → balanced/fast |
| Diagram deterministic xấu | Chỉ dùng LLM diagram khi quality hoặc diagram quá nghèo |
| cmgn_light thiếu kiểm chứng | Chạy deterministic sanitize thay cho critics |
| API bị phá | Default mode=balanced nếu request cũ không gửi mode |

---

## 8. Thứ tự triển khai đề xuất

```txt
1. Thêm generation_mode vào request/env/response
2. Sửa select_mindmap_strategy theo mode
3. Thêm build_visual_diagram_by_mode
4. Đổi Visual Diagram sang deterministic-first
5. Thêm cap_mindmap_nodes/prune_tree
6. Thêm cmgn_light hoặc enable_critics=False
7. Sửa fallback chain theo mode
8. Thêm mode vào cache hash
9. Thêm timing logs
10. Test 3 case fast/balanced/quality
```

---

# Super Prompt triển khai cho Cursor

```txt
SUPER PROMPT — Tối ưu Mindmap Generation để sinh nhanh hơn nhưng vẫn đủ ý

Mục tiêu:
Tối ưu backend mindmap generation pipeline. Không sửa frontend layout/edge hiện tại. Không phá API cũ. Thêm mode sinh mindmap để giảm số LLM calls, tăng tốc sinh mindmap, vẫn giữ đủ ý cho UI.

Các file cần xem trước khi sửa:
- BE/mindmap_generation_worker.py
- BE/mindmap_utils.py
- BE/main.py hoặc routes mindmap nếu endpoint nằm ở file khác
- BE/jobs_store.py nếu progress/job status nằm ở đây

Hiện trạng:
Pipeline hiện tại:
1. Load chunks từ index.json
2. Merge sub-chunks
3. Select strategy: single_call_schema, mindmap_v2, cmgn, iterative, multilevel
4. Sinh flat_nodes
5. Gọi thêm LLM để tạo Visual Diagram
6. Lưu mindmaps.json + update cache

Vấn đề:
- Visual Diagram đang gọi LLM riêng, tốn thêm khoảng 10-20s.
- cmgn có 5 LLM calls vì có 3 critics.
- iterative có thể rất nhiều LLM calls.
- fallback chain có thể rơi vào strategy nặng dù user chỉ cần sinh nhanh.
- Cần sinh nhanh hơn nhưng vẫn đủ ý.

Yêu cầu lớn:
1. Thêm generation mode: fast, balanced, quality.
2. Default mode là balanced nếu request cũ không gửi mode.
3. Fast/balanced không gọi LLM Visual Diagram mặc định.
4. Fast/balanced không chạy critics mặc định.
5. Quality giữ workflow chất lượng cao hiện tại.
6. Cache có thêm generation_mode để tránh dùng nhầm cache.
7. Output nodes được giới hạn hợp lý theo mode.
8. Response vẫn giữ format cũ, chỉ thêm mode nếu được.
9. Thêm timing logs để đo chính xác thời gian.
10. Build/run backend không lỗi.

==================================================
PHẦN 1: THÊM GENERATION MODE
==================================================

Trong endpoint generate mindmap hoặc run_mindmap_generation, lấy mode từ request/env:

generation_mode = payload.get("mode") or os.getenv("MINDMAP_GENERATION_MODE", "balanced")

Normalize:
- nếu không thuộc {"fast", "balanced", "quality"} thì dùng "balanced".

Không phá request cũ.

Response mindmap_record thêm:
"mode": generation_mode

==================================================
PHẦN 2: SỬA STRATEGY SELECTION THEO MODE
==================================================

Sửa hàm select_mindmap_strategy thành:

def select_mindmap_strategy(chunks, force_strategy=None, mode="balanced"):
    if force_strategy:
        return force_strategy

    n_chunks = len(chunks)
    total_chars = sum(len(c.get("text", "") if isinstance(c, dict) else str(c)) for c in chunks)

    if mode == "fast":
        if total_chars < 12000:
            return "single_call_schema"
        return "mindmap_v2"

    if mode == "balanced":
        if total_chars < 8000:
            return "single_call_schema"
        if n_chunks <= 25 and total_chars < 35000:
            return "mindmap_v2"
        if n_chunks <= 60 and total_chars < 80000:
            return "cmgn_light"
        return "multilevel_fast"

    # quality mode giữ logic sâu hơn
    if total_chars < 2500:
        return "single_call_schema"
    if n_chunks <= 4 and total_chars < 8000:
        return "single_call_schema"
    if n_chunks <= 18 and total_chars < 18000:
        return "mindmap_v2"
    if n_chunks <= 45 and total_chars < 50000:
        return "cmgn"
    return "iterative"

Ghi log:
[MindMap] mode=balanced selected_strategy=mindmap_v2 chunks=12 chars=15000

==================================================
PHẦN 3: THÊM CMGN LIGHT
==================================================

Nếu generate_mindmap_cmgn hiện có 3 critics, sửa thành nhận tham số:

generate_mindmap_cmgn(chunks, model=MINDMAP_MODEL, enable_critics=True)

Nếu enable_critics=False:
- chạy coreference graph
- chạy mindmap generation
- bỏ factuality critic
- bỏ local critic
- bỏ global critic
- chạy deterministic sanitize/postprocess

Nếu khó sửa function cũ, tạo wrapper:

def generate_mindmap_cmgn_light(chunks, model):
    return generate_mindmap_cmgn(chunks, model=model, enable_critics=False)

Mapping:
- balanced + strategy cmgn_light → enable_critics=False
- quality + strategy cmgn → enable_critics=True
- fast không dùng cmgn mặc định

==================================================
PHẦN 4: VISUAL DIAGRAM DETERMINISTIC-FIRST
==================================================

Hiện _build_visual_diagram đang gọi LLM.
Rename hàm LLM cũ thành:
_build_visual_diagram_llm(...)

Tạo wrapper mới:

def build_visual_diagram_by_mode(flat_nodes, final_chunks, root_title, sources, mode):
    if mode == "fast":
        return _flat_nodes_to_visual_diagram(flat_nodes, root_title=root_title, sources=sources)

    if mode == "balanced":
        diagram = _flat_nodes_to_visual_diagram(flat_nodes, root_title=root_title, sources=sources)
        if len(flat_nodes) < 8 or _diagram_quality_low(diagram):
            return _build_visual_diagram_llm(flat_nodes, final_chunks, root_title, sources)
        return diagram

    return _build_visual_diagram_llm(flat_nodes, final_chunks, root_title, sources)

Nếu _diagram_quality_low chưa có thì tạo simple check:
- diagram không có nodes
- diagram nodes < 5
- diagramType missing
- title missing

Quan trọng:
- fast không bao giờ gọi visual LLM.
- balanced chỉ gọi visual LLM khi diagram quá nghèo.
- quality luôn gọi visual LLM.

==================================================
PHẦN 5: CAP NODES THEO MODE
==================================================

Thêm hàm:

def cap_mindmap_nodes(flat_nodes, mode):
    if mode == "fast":
        return prune_tree(flat_nodes, max_total=35, max_depth=2, max_children_per_node=6)
    if mode == "balanced":
        return prune_tree(flat_nodes, max_total=55, max_depth=3, max_children_per_node=7)
    return prune_tree(flat_nodes, max_total=90, max_depth=4, max_children_per_node=10)

Tạo prune_tree:
- giữ root
- giữ parent trước child
- không để parent trỏ tới node đã bị xóa
- ưu tiên nodes level thấp
- trong cùng parent, giữ theo order gốc
- remove duplicate title cùng parent
- nếu cắt con, thêm hidden_count vào parent nếu thuận tiện

Gọi cap sau khi flat_nodes được sinh và sanitize xong, trước khi build visual diagram.

==================================================
PHẦN 6: CONTEXT COMPRESSION
==================================================

Trong mindmap_v2/single_call prompt:
- Không gửi full text quá dài nếu mode fast/balanced.
- Tạo cluster summaries ngắn bằng TF-IDF/representative sentences.

Giới hạn context:
fast: 12000 chars
balanced: 20000 chars
quality: 40000 chars hoặc giữ hiện tại

Nếu đã có _cluster_and_label_no_llm thì tận dụng lại.

==================================================
PHẦN 7: CACHE THEO MODE
==================================================

Sửa content_hash để bao gồm:
- generation_mode
- selected_strategy
- MINDMAP_MODEL
- schema_version
- EMBEDDING_MODEL_NAME
- embedding_dim
- visual_diagram_mode

Không dùng nhầm cache fast/balanced/quality.

Nhưng có thể reuse:
- Nếu có cache quality, có thể prune để trả balanced/fast nếu muốn.
- Nếu chưa muốn implement reuse phức tạp, chỉ cần thêm mode vào hash trước.

==================================================
PHẦN 8: FALLBACK CHAIN THEO MODE
==================================================

Fast fallback:
mindmap_v2
→ single_call_schema
→ deterministic main branches

Balanced fallback:
mindmap_v2
→ cmgn_light
→ multilevel_fast
→ single_call_schema
→ deterministic main branches

Quality fallback:
giữ fallback chain hiện tại.

Không để fast mode rơi vào iterative hoặc cmgn full.

==================================================
PHẦN 9: TIMING LOGS
==================================================

Thêm timing bằng time.perf_counter().

Log cuối:
[MindMap Timing] mode=balanced strategy=mindmap_v2 nodes=42 total=18.2s load=0.1s merge=0.2s mindmap=14.5s visual=0.1s cache=0.0s

Các mốc:
- load chunks
- merge subchunks
- cache lookup
- strategy select
- mindmap generation
- cap nodes
- visual diagram
- save
- total

==================================================
PHẦN 10: PROGRESS MESSAGE
==================================================

Update progress messages:
- Đang chọn chế độ sinh mindmap...
- Đang gom ý chính...
- Đang tạo mindmap nhanh...
- Đang tạo visual diagram nhanh...
- Đang lưu mindmap...

Fast/balanced nếu dùng deterministic visual diagram thì message không được nói đang gọi LLM.

==================================================
PHẦN 11: API COMPATIBILITY
==================================================

Không phá API cũ.

Nếu frontend chưa gửi mode:
- backend dùng balanced.

Nếu frontend gửi:
{
  "q": "...",
  "sources": [...],
  "mode": "fast"
}

Response vẫn có nodes/diagram/strategy như cũ, thêm mode.

==================================================
PHẦN 12: TEST
==================================================

Test 4 case:

1. Small doc:
- 3 chunks, 5k chars
- mode fast
- strategy single_call_schema
- visual deterministic
- expected < 15s

2. Medium doc:
- 12 chunks, 15k chars
- mode balanced
- strategy mindmap_v2
- visual deterministic
- expected < 20s

3. Large doc:
- 30 chunks, 40k chars
- mode balanced
- strategy cmgn_light hoặc mindmap_v2
- no critics
- no visual LLM
- expected giảm mạnh so với cmgn full

4. Quality:
- mode quality
- cmgn full vẫn chạy được
- visual LLM enabled

==================================================
PHẦN 13: BÁO CÁO
==================================================

Sau khi sửa xong báo:
1. Đã thêm mode nào.
2. Default mode là gì.
3. Strategy selection mới theo mode.
4. Fast/balanced có còn gọi Visual Diagram LLM không.
5. Critics còn chạy khi nào.
6. Output node cap theo mode.
7. Cache hash đã thêm mode chưa.
8. Timing log mẫu.
9. Test result.
```
