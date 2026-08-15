import { useCallback, useRef, useState } from "react";
import { streamChat } from "./streamChat";
import { isRenderEvent, type CardSpec, type UIEvent } from "../render/types";

type Message = { role: "user" | "assistant"; text: string };

type Props = {
  sessionId: string;
  onRender: (card: CardSpec) => void;
};

// The persistent Chat dock. Streams a turn from /api/chat and folds events
// into the transcript; `render` events are handed up to the Canvas, which
// owns Card spawning. Phase 0 has no history persistence — one live turn.
export default function ChatDock({ sessionId, onRender }: Props) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);

  const appendToAssistant = useCallback((chunk: string) => {
    setMessages((prev) => {
      const next = [...prev];
      const last = next[next.length - 1];
      if (last && last.role === "assistant") {
        next[next.length - 1] = { ...last, text: last.text + chunk };
      } else {
        next.push({ role: "assistant", text: chunk });
      }
      return next;
    });
  }, []);

  const handleEvent = useCallback(
    (event: UIEvent) => {
      switch (event.type) {
        case "text_delta":
          appendToAssistant(String(event.text ?? ""));
          break;
        case "tool_call":
          appendToAssistant(`\n\n_→ ${String(event.name ?? "tool")}_\n\n`);
          break;
        case "error":
          appendToAssistant(`\n\n⚠️ ${String(event.message ?? "error")}`);
          break;
        default:
          if (isRenderEvent(event)) onRender(event.card);
      }
    },
    [appendToAssistant, onRender],
  );

  const inputRef = useRef<HTMLTextAreaElement>(null);

  const send = useCallback(async () => {
    const prompt = input.trim();
    if (!prompt || busy) return;
    setInput("");
    setMessages((prev) => [
      ...prev,
      { role: "user", text: prompt },
      { role: "assistant", text: "" },
    ]);
    setBusy(true);
    try {
      await streamChat({ sessionId, prompt }, handleEvent);
    } catch (err) {
      appendToAssistant(`\n\n⚠️ ${String(err)}`);
    } finally {
      setBusy(false);
      inputRef.current?.focus();
    }
  }, [input, busy, sessionId, handleEvent, appendToAssistant]);

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void send();
    }
  };

  return (
    <div className="chat-dock">
      <div className="chat-header">artmind canvas</div>
      <div className="chat-log">
        {messages.length === 0 && (
          <div className="chat-hint">
            Try <code>/render-test README.md</code> to spawn a document Card.
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`chat-msg chat-msg-${m.role}`}>
            {m.text || (busy && i === messages.length - 1 ? "…" : "")}
          </div>
        ))}
      </div>
      <div className="chat-input-row">
        <textarea
          ref={inputRef}
          className="chat-input"
          value={input}
          placeholder="Message artmind…"
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
          rows={2}
        />
        <button className="chat-send" onClick={() => void send()} disabled={busy}>
          {busy ? "…" : "Send"}
        </button>
      </div>
    </div>
  );
}
