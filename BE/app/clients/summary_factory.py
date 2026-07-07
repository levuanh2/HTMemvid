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
        from services.summary.pipeline.summarize import summarize_sections
        return summarize_sections(
            mm_input, sections, model=None, length_mode=length_mode,
            timeout_sec=self._timeout(),
            max_workers=int(os.getenv("SUMMARY_PARALLEL", "2")),
            progress_cb=progress_cb, cancel_cb=cancel_cb)

    def synthesize(self, sections, *, doc_title, length_mode="medium"):
        from services.summary.pipeline.synthesize import synthesize
        return synthesize(sections, doc_title=doc_title, model=None,
                          length_mode=length_mode, timeout_sec=self._timeout())


def get_summary_pipeline():
    return LocalSummaryPipeline()
