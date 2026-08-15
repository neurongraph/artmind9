import type { UIEvent } from "../render/types";

// Consumes the backend's SSE stream the way artmind's chat UI does: a POST
// with a JSON body, read via `fetch` + `response.body.getReader()` (NOT
// EventSource, which is GET-only). Frames are `data: <json>\n\n`; we buffer
// across chunk boundaries and dispatch one parsed UIEvent per frame.
export async function streamChat(
  params: { sessionId: string; prompt: string; backend?: string },
  onEvent: (event: UIEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      session_id: params.sessionId,
      prompt: params.prompt,
      backend: params.backend ?? "claude-sdk",
    }),
    signal,
  });

  if (!response.ok || !response.body) {
    throw new Error(`chat request failed: ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      const line = frame.trim();
      if (!line.startsWith("data:")) continue;
      const json = line.slice(5).trim();
      if (!json) continue;
      try {
        onEvent(JSON.parse(json) as UIEvent);
      } catch {
        // Ignore malformed frames rather than tearing down the stream.
      }
    }
  }
}
