// SummaryModal v2 — record section-first (overview + sections + citation chips
// mở EvidenceDrawer) + fallback legacy (summary_md từ summaries.json migrate).
// Record được BE tự persist khi job xong — không còn nút Lưu.
import { useCallback, useState } from "react";
import Modal from "../ui/Modal";
import { MdProse } from "../ui/Markdown";
import EvidenceDrawer from "../mindmap/EvidenceDrawer";
import { Icon } from "../ui/Icon";
import { normalizeSummaryRecord } from "../../utils/summaryJob";

const CHIP_CAP = 6;

const LENGTH_LABELS = { short: "Ngắn", medium: "Vừa", detailed: "Chi tiết" };

export default function SummaryModal({ data, onClose }) {
  const [drawerNode, setDrawerNode] = useState(null);
  const closeDrawer = useCallback(() => setDrawerNode(null), []);

  const rec = normalizeSummaryRecord(data);
  if (!rec) return null;

  const degraded = Boolean(rec.generator?.degraded);
  const missing = Array.isArray(rec.generator?.missing) ? rec.generator.missing : [];
  const lengthLabel = LENGTH_LABELS[rec.lengthMode];

  const openEvidence = (section) =>
    setDrawerNode({
      id: section.id,
      title: section.title,
      note: "",
      chunkRefs: Array.isArray(section.chunk_refs) ? section.chunk_refs : [],
    });

  return (
    <Modal
      title={rec.title || "Tóm tắt tài liệu"}
      subtitle={[
        rec.sources?.length ? `${rec.sources.length} tài liệu` : null,
        lengthLabel ? `độ dài: ${lengthLabel}` : null,
      ].filter(Boolean).join(" · ") || undefined}
      onClose={onClose}
      maxWidth={840}
    >
      {/* relative để EvidenceDrawer (absolute inset-0) phủ đúng vùng nội dung modal */}
      <div className="relative">
        <div className="p-5">
          {degraded && (
            <div className="mb-4 rounded-[7px] border px-3 py-2.5 text-[12.5px]"
              style={{ borderColor: "var(--warn)", background: "color-mix(in srgb, var(--warn) 8%, transparent)", color: "var(--text-secondary)" }}>
              <Icon name="TriangleAlert" size={13} className="inline-block mr-1.5 align-[-2px]" />
              Một số phần chưa tóm tắt được{missing.length ? `: ${missing.join(", ")}` : "."} Bạn có thể tạo lại để thử lần nữa.
            </div>
          )}

          {rec.sections.length > 0 ? (
            <>
              {rec.overview && (
                <>
                  <div className="font-mono text-[11px] uppercase tracking-[0.12em] text-text-muted mb-2">Tổng quan</div>
                  <div className="surface-card font-reading mb-4">
                    <MdProse text={rec.overview} />
                  </div>
                </>
              )}

              <div className="font-mono text-[11px] uppercase tracking-[0.12em] text-text-muted mb-2">Theo mục</div>
              <div className="flex flex-col gap-3">
                {rec.sections.map((s) => {
                  const refs = Array.isArray(s.chunk_refs) ? s.chunk_refs : [];
                  return (
                    <section key={s.id} className="surface-card font-reading">
                      <h3 className="font-display text-[15.5px] font-semibold text-text-primary mb-2">{s.title}</h3>
                      {s.summary
                        ? <MdProse text={s.summary} />
                        : <p className="text-[13px] italic text-text-muted">Mục này chưa tóm tắt được.</p>}
                      {Array.isArray(s.key_points) && s.key_points.length > 0 && (
                        <ul className="pl-5 mt-2.5 list-disc marker:text-slate text-[14px] text-text-primary">
                          {s.key_points.map((p, i) => <li key={i} className="mb-1 leading-[1.6]">{p}</li>)}
                        </ul>
                      )}
                      {refs.length > 0 && (
                        <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
                          {refs.slice(0, CHIP_CAP).map((r) => (
                            <button
                              key={r}
                              onClick={() => openEvidence(s)}
                              className="cite-chip !text-[11px]"
                              title={`Xem bằng chứng: đoạn ${r}`}
                            >
                              đoạn {r}
                            </button>
                          ))}
                          {refs.length > CHIP_CAP && (
                            <button onClick={() => openEvidence(s)} className="text-[11px] text-text-muted hover:text-accent underline">
                              +{refs.length - CHIP_CAP} đoạn
                            </button>
                          )}
                        </div>
                      )}
                    </section>
                  );
                })}
              </div>

              {rec.entities.length > 0 && (
                <div className="mt-4">
                  <div className="font-mono text-[11px] uppercase tracking-[0.12em] text-text-muted mb-2">Khái niệm then chốt</div>
                  <div className="flex flex-wrap gap-1.5">
                    {rec.entities.map((e, i) => (
                      <span key={i} className="pill-tab !px-2.5 !py-1 !cursor-default">{e}</span>
                    ))}
                  </div>
                </div>
              )}
            </>
          ) : (
            <>
              <div className="font-mono text-[11px] uppercase tracking-[0.12em] text-text-muted mb-2">Bản tóm tắt</div>
              <div className="surface-card font-reading">
                <MdProse text={rec.legacyMd || "Không có tóm tắt."} />
              </div>
            </>
          )}
        </div>

        {drawerNode && <EvidenceDrawer node={drawerNode} onClose={closeDrawer} />}
      </div>
    </Modal>
  );
}
