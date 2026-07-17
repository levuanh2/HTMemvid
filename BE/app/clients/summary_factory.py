"""Adapter pipeline Summary v2 — local monolith (mirror mindmap_factory, không gRPC)."""
from __future__ import annotations

import os


class LocalSummaryPipeline:
    def _timeout(self) -> float:
        return float(os.getenv("SUMMARY_LLM_TIMEOUT_SEC", "120"))

    def sections(self, mm_input):
        from services.mindmap.pipeline.outline import build_outline
        from services.summary.pipeline.sections import build_sections

        def _outline(mi):
            # model=None → ask_ai tự resolve theo feature; timeout dùng chung summary
            return build_outline(mi, model=None, timeout_sec=self._timeout())

        return build_sections(mm_input, outline_fn=_outline)

    def summarize(self, mm_input, sections, *, length_mode="medium",
                  progress_cb=None, cancel_cb=None):
        from shared.config import get_settings
        from services.summary.pipeline.summarize import summarize_sections
        return summarize_sections(
            mm_input, sections, model=None, length_mode=length_mode,
            timeout_sec=self._timeout(),
            max_workers=int(os.getenv("SUMMARY_PARALLEL", "2")),
            with_facts=get_settings().summary_facts,
            progress_cb=progress_cb, cancel_cb=cancel_cb)

    def synthesize(self, sections, *, doc_title, length_mode="medium"):
        from services.summary.pipeline.synthesize import synthesize
        return synthesize(sections, doc_title=doc_title, model=None,
                          length_mode=length_mode, timeout_sec=self._timeout())

    def coverage(self, record):
        """Phase 5 judge-only. Tắt (SUMMARY_COVERAGE=0) → None (không judge, không LLM).
        Bật → 1 LLM judge chấm coverage → dict chẩn đoán; lỗi/JSON hỏng → None (không
        làm hỏng job). KHÔNG viết lại summary, KHÔNG auto-repair."""
        from shared.config import get_settings
        if not get_settings().summary_coverage:
            return None
        from concurrent.futures import ThreadPoolExecutor
        from app.clients.llm_factory import ask_ai
        from services.summary.pipeline.coverage import COVERAGE_SYSTEM, judge_coverage
        timeout = self._timeout()

        def _ask(prompt):
            from app.graphs.logger import ctx_submit  # Phase 0: propagate LLM counter
            ex = ThreadPoolExecutor(max_workers=1)
            try:
                fut = ctx_submit(ex, ask_ai, prompt, system_prompt=COVERAGE_SYSTEM,
                                 model=None, feature="summary", options={"temperature": 0})
                return fut.result(timeout=timeout)
            finally:
                ex.shutdown(wait=False)     # timeout phải trả ngay (bài học warmup)

        return judge_coverage(record, ask_fn=_ask, enabled=True)


def get_summary_pipeline():
    return LocalSummaryPipeline()
