import { describe, it, expect } from "vitest";
import {
  isInteractiveTarget,
  shouldFocusComposer,
  shouldRefocusComposer,
  INTERACTIVE_SELECTOR,
} from "./chatFocus";

// Element giả mô phỏng closest() thật: `hits` là danh sách selector coi như khớp.
// Dùng để chạm ĐÚNG nhánh production (closest), không chỉ nhánh fallback tagName.
const el = (tagName, { hits = [], isContentEditable = false } = {}) => ({
  tagName,
  isContentEditable,
  closest: (sel) => (hits.includes(sel) ? { tagName } : null),
});

describe("isInteractiveTarget", () => {
  it("đường closest: bắt cả khi bấm trúng con bên trong nút", () => {
    // <button><svg/></button> — target là svg, không phải button.
    expect(isInteractiveTarget(el("svg", { hits: [INTERACTIVE_SELECTOR] }))).toBe(true);
  });

  it("đường closest: vùng trống → false", () => {
    expect(isInteractiveTarget(el("DIV"))).toBe(false);
    expect(isInteractiveTarget(el("P"))).toBe(false);
  });

  it("fallback tagName khi không có closest", () => {
    expect(isInteractiveTarget({ tagName: "BUTTON" })).toBe(true);
    expect(isInteractiveTarget({ tagName: "A" })).toBe(true);
    expect(isInteractiveTarget({ tagName: "TEXTAREA" })).toBe(true);
    expect(isInteractiveTarget({ tagName: "DIV" })).toBe(false);
    expect(isInteractiveTarget({ tagName: "DIV", isContentEditable: true })).toBe(true);
  });

  it("null/undefined → false", () => {
    expect(isInteractiveTarget(null)).toBe(false);
    expect(isInteractiveTarget(undefined)).toBe(false);
  });

  it("selector có mặt các surface không được cướp focus", () => {
    for (const s of ["button", "a", '[role="button"]', ".cite-chip", ".evidence-frame", "[contenteditable]"]) {
      expect(INTERACTIVE_SELECTOR).toContain(s);
    }
  });
});

describe("shouldFocusComposer", () => {
  const blank = () => el("DIV");

  it("click vùng trống → focus", () => {
    expect(shouldFocusComposer(blank())).toBe(true);
  });

  it("click nút/link/chip trích dẫn/thẻ nguồn → KHÔNG cướp focus", () => {
    expect(shouldFocusComposer(el("BUTTON", { hits: [INTERACTIVE_SELECTOR] }))).toBe(false);
    expect(shouldFocusComposer(el("SUP", { hits: [INTERACTIVE_SELECTOR] }))).toBe(false); // .cite-chip
    expect(shouldFocusComposer({ tagName: "A" })).toBe(false);
  });

  it("đang bôi đen text → KHÔNG focus (giữ selection để copy)", () => {
    expect(shouldFocusComposer(blank(), { hasSelection: true })).toBe(false);
  });

  it("con trỏ thô (cảm ứng) → KHÔNG focus (không bật bàn phím ảo)", () => {
    expect(shouldFocusComposer(blank(), { coarsePointer: true })).toBe(false);
  });

  it("ô nhập đang disabled (loading) → KHÔNG focus", () => {
    expect(shouldFocusComposer(blank(), { disabled: true })).toBe(false);
  });
});

describe("shouldRefocusComposer", () => {
  const composer = { tagName: "TEXTAREA" };
  const body = { tagName: "BODY" };

  it("focus đang ở body (vừa gửi xong, textarea bị disable nên trình duyệt nhả focus) → refocus", () => {
    expect(shouldRefocusComposer({ activeElement: body, body, composer })).toBe(true);
  });

  it("focus vốn đã ở ô nhập → vẫn refocus (no-op an toàn)", () => {
    expect(shouldRefocusComposer({ activeElement: composer, body, composer })).toBe(true);
  });

  it("người dùng đang đứng ở chỗ khác → KHÔNG giật focus", () => {
    // EvidenceDrawer tự focus nút đóng — playbook có ghi bug cũ đúng loại này.
    const closeBtn = { tagName: "BUTTON" };
    expect(shouldRefocusComposer({ activeElement: closeBtn, body, composer })).toBe(false);
  });

  it("chưa có ref ô nhập → false", () => {
    expect(shouldRefocusComposer({ activeElement: body, body, composer: null })).toBe(false);
  });

  it("cảm ứng / disabled → false", () => {
    expect(shouldRefocusComposer({ activeElement: body, body, composer, coarsePointer: true })).toBe(false);
    expect(shouldRefocusComposer({ activeElement: body, body, composer, disabled: true })).toBe(false);
  });
});
