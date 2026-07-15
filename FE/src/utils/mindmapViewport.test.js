import { describe, it, expect } from "vitest";
import {
  clampScale,
  nextScale,
  formatZoom,
  viewportKeyAction,
  DEFAULT_SCALE_MIN,
  DEFAULT_SCALE_MAX,
  ZOOM_STEP,
} from "./mindmapViewport";

const RANGE = { min: 0.2, max: 1.4 };

describe("clampScale", () => {
  it("giữ nguyên giá trị trong khoảng", () => {
    expect(clampScale(1, RANGE)).toBe(1);
    expect(clampScale(0.75, RANGE)).toBe(0.75);
  });

  it("kẹp theo min/max", () => {
    expect(clampScale(0.05, RANGE)).toBe(0.2);
    expect(clampScale(9, RANGE)).toBe(1.4);
    expect(clampScale(0.2, RANGE)).toBe(0.2);
    expect(clampScale(1.4, RANGE)).toBe(1.4);
  });

  it("mặc định mirror hằng số mind-elixir khi không truyền range", () => {
    expect(DEFAULT_SCALE_MIN).toBe(0.2);
    expect(DEFAULT_SCALE_MAX).toBe(1.4);
    expect(clampScale(99)).toBe(DEFAULT_SCALE_MAX);
    expect(clampScale(0)).toBe(DEFAULT_SCALE_MIN);
  });

  it("non-finite → 1 (không bao giờ nhả NaN vào CSS transform)", () => {
    // scaleFit() chia offsetHeight cho container ẩn → 0/0 = NaN (xem comment trong module).
    expect(clampScale(NaN, RANGE)).toBe(1);
    expect(clampScale(undefined, RANGE)).toBe(1);
    expect(clampScale(Infinity, RANGE)).toBe(1);
  });

  it("range non-finite → rơi về mặc định", () => {
    expect(clampScale(99, { min: NaN, max: NaN })).toBe(DEFAULT_SCALE_MAX);
  });
});

describe("nextScale", () => {
  it("cộng delta", () => {
    expect(nextScale(1, 0.2, RANGE)).toBeCloseTo(1.2);
    expect(nextScale(1, -0.2, RANGE)).toBeCloseTo(0.8);
  });

  it("kẹp ở trần/sàn thay vì vượt", () => {
    // Guard `scale()` của mind-elixir là REJECT (no-op) chứ không phải clamp:
    // vượt max = nút bấm im lặng không làm gì. Kẹp trước để luôn tới đúng trần.
    expect(nextScale(1.3, 0.2, RANGE)).toBe(1.4);
    expect(nextScale(1.4, 0.2, RANGE)).toBe(1.4);
    expect(nextScale(0.3, -0.2, RANGE)).toBe(0.2);
    expect(nextScale(0.2, -0.2, RANGE)).toBe(0.2);
  });

  it("current non-finite → coi như 1", () => {
    expect(nextScale(undefined, 0.2, RANGE)).toBeCloseTo(1.2);
    expect(nextScale(NaN, -0.2, RANGE)).toBeCloseTo(0.8);
  });

  it("ZOOM_STEP giữ nguyên bước 0.2 của zoomBy cũ", () => {
    expect(ZOOM_STEP).toBe(0.2);
  });
});

describe("formatZoom", () => {
  it("định dạng phần trăm", () => {
    expect(formatZoom(1)).toBe("100%");
    expect(formatZoom(0.4)).toBe("40%");
    expect(formatZoom(1.4)).toBe("140%");
    expect(formatZoom(0.2)).toBe("20%");
  });

  it("làm tròn", () => {
    expect(formatZoom(0.756)).toBe("76%");
    expect(formatZoom(1.2000000000000002)).toBe("120%"); // rác float từ cộng dồn delta
    expect(formatZoom(0.334)).toBe("33%");
  });

  it("non-finite → 100% (không in NaN%)", () => {
    expect(formatZoom(NaN)).toBe("100%");
    expect(formatZoom(undefined)).toBe("100%");
  });
});

describe("viewportKeyAction", () => {
  const key = (k, mods = {}) => ({ key: k, ...mods });

  it("map phím sang hành động", () => {
    expect(viewportKeyAction(key("+"))).toBe("in");
    expect(viewportKeyAction(key("="))).toBe("in");
    expect(viewportKeyAction(key("-"))).toBe("out");
    expect(viewportKeyAction(key("_"))).toBe("out");
    expect(viewportKeyAction(key("0"))).toBe("reset");
    expect(viewportKeyAction(key("f"))).toBe("fit");
    expect(viewportKeyAction(key("F"))).toBe("fit");
  });

  it("phím lạ → null", () => {
    expect(viewportKeyAction(key("a"))).toBeNull();
    expect(viewportKeyAction(key("1"))).toBeNull();
    expect(viewportKeyAction(key("Escape"))).toBeNull(); // Escape vẫn thuộc requestClose
    expect(viewportKeyAction(null)).toBeNull();
  });

  it("đang gõ → null", () => {
    expect(viewportKeyAction(key("f"), { isEditing: true })).toBeNull();
    expect(viewportKeyAction(key("0"), { isEditing: true })).toBeNull();
    expect(viewportKeyAction(key("+"), { isEditing: true })).toBeNull();
  });

  it("có ctrl/meta/alt → null (nhường keymap sẵn có của mind-elixir)", () => {
    expect(viewportKeyAction(key("=", { ctrlKey: true }))).toBeNull();
    expect(viewportKeyAction(key("-", { metaKey: true }))).toBeNull();
    expect(viewportKeyAction(key("0", { ctrlKey: true }))).toBeNull();
    expect(viewportKeyAction(key("f", { altKey: true }))).toBeNull();
  });

  it("shift KHÔNG chặn — '+' cần shift trên nhiều layout", () => {
    expect(viewportKeyAction(key("+", { shiftKey: true }))).toBe("in");
  });
});
