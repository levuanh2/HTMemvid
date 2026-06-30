// Shared logic for the signature: the retrieval apparatus (pipeline trace)
// and the evidence margin (citation ⇄ source-frame linking).

// ── Retrieval pipeline node labels (Vietnamese) ──────────────────
// Keys mirror `current_node` values emitted by BE/app/graphs/query_graph.py.
export const NODE_LABELS = {
  Queued:          "Đang xếp hàng",
  CheckSources:    "Kiểm tra tài liệu",
  CacheLookup:     "Tra bộ nhớ đệm",
  RetrieveMemory:  "Truy hồi cây trí nhớ",
  RetrieveFAISS:   "Tìm trong chỉ mục ngữ nghĩa",
  RerankDocuments: "Xếp hạng lại bằng chứng",
  VerifyContext:   "Đối chiếu mâu thuẫn (NLI)",
  ContextBuilder:  "Ghép ngữ cảnh",
  GenerateAnswer:  "Soạn câu trả lời",
  Evaluate:        "Chấm điểm câu trả lời",
  FeedbackLoop:    "Tinh chỉnh",
  Finalize:        "Hoàn tất",
};

export function nodeLabel(key) {
  if (!key) return "Đang xử lý";
  return NODE_LABELS[key] || String(key);
}

// ── Canonical source-stem matching (mirrors ChatArea/BE stem logic) ──
export function normStem(s) {
  return String(s || "").trim().toLowerCase().replace(/_\d{8}_\d{6}$/, "");
}

export function citeKey(stem, chunkId) {
  return `${normStem(stem)}::${String(chunkId ?? "")}`;
}

// ── Citation linkifying ──────────────────────────────────────────
// The query graph annotates context chunks as "[Nguồn: <stem>, đoạn <id>]".
// If the answer reproduces those markers, turn each into a numbered chip
// that links to its source frame; the registry is reused by the margin.
const CITE_RE = /\[\s*Nguồn\s*:\s*([^,\]]+?)\s*,\s*đoạn\s*([0-9]+)\s*\]/gi;

export function processCitations(answer) {
  const citations = [];
  const index = new Map(); // citeKey -> n
  const md = String(answer || "").replace(CITE_RE, (_, stem, chunk) => {
    const s = String(stem).trim();
    const key = citeKey(s, chunk);
    let n = index.get(key);
    if (!n) {
      n = citations.length + 1;
      index.set(key, n);
      citations.push({ n, stem: s, chunkId: String(chunk) });
    }
    return `[${n}](#cite:${encodeURIComponent(s)}:${chunk})`;
  });
  return { md, citations, index };
}

// Decode an `a` href produced by processCitations → { stem, chunkId } | null
export function parseCiteHref(href) {
  if (typeof href !== "string" || !href.startsWith("#cite:")) return null;
  const rest = href.slice("#cite:".length);
  const lastColon = rest.lastIndexOf(":");
  if (lastColon < 0) return null;
  return {
    stem: decodeURIComponent(rest.slice(0, lastColon)),
    chunkId: rest.slice(lastColon + 1),
  };
}
