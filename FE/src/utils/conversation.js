// Conversation id helper for the chat thread.
//
// The id is sent as `session_id` on every /query and identifies the conversation
// on the backend. "New chat" rotates it (a fresh thread with empty context);
// "Clear context" and "Delete history" keep the same id but change what the
// backend does with the prior turns.

export function newConversationId() {
  try {
    if (typeof crypto !== "undefined" && crypto.randomUUID) return crypto.randomUUID();
  } catch {}
  // Fallback for environments without crypto.randomUUID.
  return "conv-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
}
