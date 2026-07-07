// Guard hồi quy Phase 1: MindElixir.css tiêu thụ đủ bộ cssVar KHÔNG fallback —
// thiếu var nào là layout/màu sụp (bài học "chỉ set 4/21 var").
import { describe, it, expect } from "vitest";
import { THEME } from "./MindElixirView";

const REQUIRED_VARS = [
  "--map-padding", "--main-gap-x", "--main-gap-y", "--node-gap-x", "--node-gap-y",
  "--root-radius", "--main-radius", "--topic-padding",
  "--root-color", "--root-bgcolor", "--root-border-color",
  "--main-color", "--main-bgcolor", "--main-border", "--main-bgcolor-transparent",
  "--color", "--bgcolor", "--selected", "--accent-color",
  "--panel-color", "--panel-bgcolor", "--panel-border-color",
];

describe("THEME PhongDoc", () => {
  it("set đủ mọi cssVar mà mind-elixir tiêu thụ", () => {
    for (const v of REQUIRED_VARS) {
      expect(THEME.cssVar[v], `thiếu ${v}`).toBeTruthy();
    }
  });
});
