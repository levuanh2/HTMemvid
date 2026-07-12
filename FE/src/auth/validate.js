// Pure auth form validation — no React, no DOM (testable in node vitest).
// Real backend auth arrives in Phase 2/3; these rules power the Login/Register
// placeholder forms now and the real forms later.

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function isValidEmail(email) {
  return typeof email === "string" && EMAIL_RE.test(email.trim());
}

// MVP rule: at least 8 characters. Kept intentionally simple; tighten later.
export function isValidPassword(password) {
  return typeof password === "string" && password.length >= 8;
}

export function passwordsMatch(a, b) {
  return typeof a === "string" && a.length > 0 && a === b;
}
