import { describe, it, expect } from "vitest";
import { newConversationId } from "./conversation";

describe("newConversationId", () => {
  it("returns a non-empty string", () => {
    const id = newConversationId();
    expect(typeof id).toBe("string");
    expect(id.length).toBeGreaterThan(8);
  });

  it("rotates — two calls produce different ids (New chat)", () => {
    expect(newConversationId()).not.toBe(newConversationId());
  });
});
