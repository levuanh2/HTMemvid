// Registry guard: Icon.jsx đổi từ `import * as Lucide` (kéo cả lib) sang danh
// sách import tường minh. Rủi ro duy nhất = quên đăng ký icon đang dùng →
// render null im lặng. Test này quét TOÀN BỘ source lấy mọi tên icon literal
// và assert từng tên có mặt trong ICONS của Icon.jsx. Thêm icon mới mà quên
// đăng ký → test đỏ ngay.
import { describe, it, expect } from "vitest";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const SRC = join(dirname(fileURLToPath(import.meta.url)), "..", "..");

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p, out);
    else if (/\.(jsx|js)$/.test(name) && !/\.test\.js$/.test(name)) out.push(p);
  }
  return out;
}

function registryNames() {
  const src = readFileSync(join(SRC, "components", "ui", "Icon.jsx"), "utf8");
  const m = src.match(/const ICONS = \{([\s\S]*?)\};/);
  expect(m, "Icon.jsx phải có object ICONS").toBeTruthy();
  return new Set(m[1].match(/[A-Za-z0-9]+/g) || []);
}

function usedNames() {
  const used = new Set();
  for (const f of walk(SRC)) {
    const text = readFileSync(f, "utf8");
    // <Icon name="X" …> / icon="X" / { icon: "X" } — mọi cách app truyền tên icon
    for (const re of [/(?:name|icon)=\{?"([A-Za-z0-9]+)"/g, /icon:\s*"([A-Za-z0-9]+)"/g]) {
      let m;
      while ((m = re.exec(text)) !== null) used.add(m[1]);
    }
  }
  return used;
}

describe("icon registry", () => {
  it("every icon name used in source is registered in ICONS", () => {
    const registry = registryNames();
    const missing = [...usedNames()].filter((n) => !registry.has(n));
    expect(missing).toEqual([]);
  });

  it("registry is non-trivial (scan actually found the app's icons)", () => {
    expect(usedNames().size).toBeGreaterThan(20);
  });
});
