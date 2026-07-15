// Chính sách focus cho khung chat. THUẦN: không import React, không đụng window/document —
// caller bơm sự thật DOM vào (target, selection, pointer) rồi hàm ở đây chỉ QUYẾT ĐỊNH.
// Tách vậy để test được ở env `node` mặc định của vitest (repo chưa có jsdom).

// Mọi thứ tự nó đã "ăn" cú click — bấm vào đây thì KHÔNG được kéo focus về ô nhập.
// `.cite-chip` (ChatArea::makeMdComponents) có role="button" + tabIndex=0 nên phải tự giữ
// focus; `.evidence-frame` (SidebarRight) là thẻ nguồn ở lề phải.
export const INTERACTIVE_SELECTOR =
  'button, a, input, textarea, select, label, [role="button"], [contenteditable], .cite-chip, .evidence-frame';

// Fallback khi element không có closest() (object giả trong test thuần).
const INTERACTIVE_TAGS = new Set(["BUTTON", "A", "INPUT", "TEXTAREA", "SELECT", "LABEL"]);

/**
 * Cú click có rơi vào thứ tương tác được không (kể cả khi bấm trúng icon/span CON
 * bên trong nút — nên phải `closest`, không chỉ xét chính target).
 */
export function isInteractiveTarget(el) {
  if (!el) return false;
  // Đường CHÍNH ở production: DOM thật luôn có closest().
  if (typeof el.closest === "function") return Boolean(el.closest(INTERACTIVE_SELECTOR));
  // Đường phụ: test thuần truyền {tagName}. Chỉ xét chính nó, không có tổ tiên để tra.
  if (el.isContentEditable) return true;
  return INTERACTIVE_TAGS.has(String(el.tagName || "").toUpperCase());
}

/**
 * Click vào vùng trống của khung chat → có focus ô nhập không?
 *
 * `coarsePointer`: cảm ứng thì KHÔNG BAO GIỜ tự focus — focus lập trình trên mobile bật
 * bàn phím ảo, mỗi lần chạm/cuộn mà bàn phím nhảy lên là hỏng trải nghiệm.
 * `hasSelection`: đang bôi đen để copy thì focus sẽ xoá selection → cấm.
 * `disabled`: textarea disabled lúc loading, .focus() là no-op vô nghĩa.
 */
export function shouldFocusComposer(target, { hasSelection = false, coarsePointer = false, disabled = false } = {}) {
  if (disabled || hasSelection || coarsePointer) return false;
  return !isInteractiveTarget(target);
}

/**
 * Có nên refocus ô nhập sau khi trả lời xong / đổi hội thoại không?
 *
 * Chốt chặn quan trọng: CHỈ nhận focus khi người dùng đang không đứng ở đâu cả
 * (`activeElement` là body/null) hoặc vốn đã ở chính ô nhập. Nếu họ đã click sang chỗ
 * khác — EvidenceDrawer (drawer tự focus nút đóng), thẻ nguồn, nút toolbar — thì giật
 * focus về là cướp. Sau khi gửi, textarea bị disabled nên trình duyệt tự đẩy focus về
 * body → điều kiện này thành đúng và refocus chạy, đúng lúc cần.
 */
export function shouldRefocusComposer({ activeElement, body, composer, coarsePointer = false, disabled = false } = {}) {
  if (disabled || coarsePointer) return false;
  if (!composer) return false;
  if (!activeElement) return true;
  return activeElement === body || activeElement === composer;
}
