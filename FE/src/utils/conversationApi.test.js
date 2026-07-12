import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { clearConversationContext, deleteConversation, getConversationMessages } from "./api";

const realFetch = globalThis.fetch;

function mockFetch(status, body = {}) {
  globalThis.fetch = vi.fn(async () => ({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  }));
}

beforeEach(() => { globalThis.fetch = vi.fn(); });
afterEach(() => { globalThis.fetch = realFetch; vi.restoreAllMocks(); });

describe("clearConversationContext", () => {
  it("POSTs to the clear-context endpoint", async () => {
    mockFetch(200, { ok: true, context_reset_at: 123 });
    const out = await clearConversationContext("abc");
    const [url, opts] = globalThis.fetch.mock.calls[0];
    expect(url).toContain("/conversations/abc/clear-context");
    expect(opts.method).toBe("POST");
    expect(out.ok).toBe(true);
  });

  it("treats 404 (feature off) as a disabled no-op, not an error", async () => {
    mockFetch(404);
    await expect(clearConversationContext("abc")).resolves.toEqual({ ok: false, disabled: true });
  });
});

describe("deleteConversation", () => {
  it("DELETEs the conversation and returns removed count", async () => {
    mockFetch(200, { ok: true, removed: 4 });
    const out = await deleteConversation("abc");
    const [url, opts] = globalThis.fetch.mock.calls[0];
    expect(url).toContain("/conversations/abc");
    expect(opts.method).toBe("DELETE");
    expect(out.removed).toBe(4);
  });

  it("404 → disabled no-op", async () => {
    mockFetch(404);
    await expect(deleteConversation("abc")).resolves.toEqual({ ok: false, disabled: true });
  });
});

describe("getConversationMessages", () => {
  it("GETs the messages list", async () => {
    mockFetch(200, { messages: [{ role: "user", content: "hi" }] });
    const out = await getConversationMessages("abc");
    const [url] = globalThis.fetch.mock.calls[0];
    expect(url).toContain("/conversations/abc/messages");
    expect(out.messages).toHaveLength(1);
  });
});
