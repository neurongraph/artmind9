import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { marked } from "marked";
import DOMPurify from "dompurify";
import { streamChat } from "./streamChat";
import { isRenderEvent, type CardSpec, type UIEvent } from "../render/types";

// A block-structured transcript that mirrors artmind's chat UI
// (artmind/webui/static/app.js) over the identical 8-event SSE wire. An
// assistant turn is a sequence of text/thinking blocks plus a tool trace;
// completed text blocks render as sanitized markdown, streaming ones stay
// plain so partial markdown doesn't flicker.
type AssistantBlock = { kind: "text" | "thinking"; text: string; done: boolean };
type ToolTrace = { id: string; name: string; input?: string; result?: string };

type UserTurn = { role: "user"; text: string };
type AssistantTurn = {
  role: "assistant";
  blocks: AssistantBlock[];
  tools: ToolTrace[];
  error?: string;
  done: boolean;
};
type Turn = UserTurn | AssistantTurn;

type Props = {
  sessionId: string;
  onRender: (card: CardSpec) => void;
  // Promote text into the Editor pane as an unsaved draft (ADR 0015): long /
  // markdown output graduates from the transcript to an editable buffer.
  onPromote: (text: string, title?: string) => void;
  onCollapse: () => void;
};

// Promotion is a deliberate graduation, not automatic: offer it once a reply is
// substantial enough that editing it in place makes sense.
const PROMOTE_MIN_CHARS = 200;

// Concatenated done+streaming text of an assistant turn — feeds the promote gate
// and the promote payload.
function assistantText(turn: AssistantTurn): string {
  return turn.blocks
    .filter((b) => b.kind === "text")
    .map((b) => b.text)
    .join("\n\n");
}

// Render one assistant text block. Done blocks are markdown (marked → DOMPurify,
// the same seam DocumentCard uses); code blocks get a copy button attached after
// render. Streaming blocks stay as pre-wrap plain text.
function TextBlockView({ block }: { block: AssistantBlock }) {
  const ref = useRef<HTMLDivElement>(null);
  const html = block.done
    ? DOMPurify.sanitize(marked.parse(block.text, { async: false }) as string)
    : "";

  useEffect(() => {
    if (!block.done || !ref.current) return;
    for (const pre of ref.current.querySelectorAll("pre")) {
      if (pre.querySelector(".copy-btn")) continue;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "copy-btn";
      btn.textContent = "copy";
      btn.addEventListener("click", async () => {
        const code = pre.querySelector("code")?.textContent ?? pre.textContent ?? "";
        try {
          await navigator.clipboard.writeText(code);
          btn.textContent = "copied";
        } catch {
          btn.textContent = "failed";
        }
        setTimeout(() => (btn.textContent = "copy"), 1200);
      });
      pre.appendChild(btn);
    }
  }, [block.done, html]);

  if (block.done) {
    return <div ref={ref} className="chat-md" dangerouslySetInnerHTML={{ __html: html }} />;
  }
  return <div className="chat-md chat-md-streaming">{block.text}</div>;
}

// The persistent Chat dock. Streams a turn from /api/chat and folds all 8 SSE
// events into a block-structured transcript; `render` events are handed up to
// the Canvas, which owns Card spawning.
export default function ChatDock({ sessionId, onRender, onPromote, onCollapse }: Props) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);

  const logRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  // Stay pinned to the bottom unless the user has scrolled up to read history.
  const stickRef = useRef(true);

  // Mutate the last (in-flight) assistant turn immutably.
  const updateAssistant = useCallback(
    (fn: (t: AssistantTurn) => AssistantTurn) => {
      setTurns((prev) => {
        const next = [...prev];
        for (let i = next.length - 1; i >= 0; i--) {
          const t = next[i];
          if (t.role === "assistant") {
            next[i] = fn(t);
            break;
          }
        }
        return next;
      });
    },
    [],
  );

  const handleEvent = useCallback(
    (event: UIEvent) => {
      switch (event.type) {
        case "thinking_delta":
          updateAssistant((t) => {
            const blocks = [...t.blocks];
            const last = blocks[blocks.length - 1];
            if (last && last.kind === "thinking" && !last.done) {
              blocks[blocks.length - 1] = { ...last, text: last.text + String(event.text ?? "") };
            } else {
              blocks.push({ kind: "thinking", text: String(event.text ?? ""), done: false });
            }
            return { ...t, blocks };
          });
          break;
        case "text_delta":
          updateAssistant((t) => {
            const blocks = t.blocks.map((b) =>
              b.kind === "thinking" && !b.done ? { ...b, done: true } : b,
            );
            const last = blocks[blocks.length - 1];
            if (last && last.kind === "text" && !last.done) {
              blocks[blocks.length - 1] = { ...last, text: last.text + String(event.text ?? "") };
            } else {
              blocks.push({ kind: "text", text: String(event.text ?? ""), done: false });
            }
            return { ...t, blocks };
          });
          break;
        case "block_done":
          updateAssistant((t) => {
            const kind = event.block === "thinking" ? "thinking" : "text";
            const blocks = [...t.blocks];
            for (let i = blocks.length - 1; i >= 0; i--) {
              if (blocks[i].kind === kind && !blocks[i].done) {
                blocks[i] = { ...blocks[i], done: true };
                break;
              }
            }
            return { ...t, blocks };
          });
          break;
        case "tool_call":
          updateAssistant((t) => ({
            ...t,
            tools: [
              ...t.tools,
              { id: String(event.id ?? ""), name: String(event.name ?? "tool"), input: str(event.input) },
            ],
          }));
          break;
        case "tool_result":
          updateAssistant((t) => ({
            ...t,
            tools: t.tools.map((tc) =>
              tc.id === String(event.tool_id ?? "") ? { ...tc, result: str(event.content) } : tc,
            ),
          }));
          break;
        case "turn_done":
          updateAssistant((t) => ({
            ...t,
            done: true,
            blocks: t.blocks.map((b) => (b.done ? b : { ...b, done: true })),
          }));
          break;
        case "error":
          updateAssistant((t) => ({
            ...t,
            error: String(event.message ?? "error"),
            blocks: t.blocks.map((b) => (b.done ? b : { ...b, done: true })),
          }));
          break;
        default:
          if (isRenderEvent(event)) onRender(event.card);
      }
    },
    [updateAssistant, onRender],
  );

  const send = useCallback(async () => {
    const prompt = input.trim();
    if (!prompt || busy) return;
    setInput("");
    if (inputRef.current) inputRef.current.style.height = "auto";
    stickRef.current = true;
    setTurns((prev) => [
      ...prev,
      { role: "user", text: prompt },
      { role: "assistant", blocks: [], tools: [], done: false },
    ]);
    setBusy(true);
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      await streamChat({ sessionId, prompt }, handleEvent, controller.signal);
    } catch (err) {
      if ((err as Error)?.name !== "AbortError") {
        updateAssistant((t) => ({
          ...t,
          error: String(err),
          blocks: t.blocks.map((b) => (b.done ? b : { ...b, done: true })),
        }));
      }
    } finally {
      // Finalize any block the stream left open (aborted or dropped mid-turn).
      updateAssistant((t) =>
        t.done ? t : { ...t, done: true, blocks: t.blocks.map((b) => (b.done ? b : { ...b, done: true })) },
      );
      abortRef.current = null;
      setBusy(false);
      inputRef.current?.focus();
    }
  }, [input, busy, sessionId, handleEvent, updateAssistant]);

  const stop = useCallback(async () => {
    abortRef.current?.abort();
    try {
      await fetch(`/api/session/${sessionId}/interrupt`, { method: "POST" });
    } catch {
      // Server may already be gone; the abort above already halted the stream.
    }
  }, [sessionId]);

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void send();
    }
  };

  const onInput = (e: React.FormEvent<HTMLTextAreaElement>) => {
    const ta = e.currentTarget;
    ta.style.height = "auto";
    ta.style.height = `${Math.min(ta.scrollHeight, 200)}px`;
  };

  // Track whether the user is pinned to the bottom.
  const onScroll = () => {
    const log = logRef.current;
    if (!log) return;
    stickRef.current = log.scrollHeight - log.scrollTop - log.clientHeight < 120;
  };

  // Autoscroll on new content only when the user hasn't scrolled up.
  useLayoutEffect(() => {
    const log = logRef.current;
    if (log && stickRef.current) log.scrollTop = log.scrollHeight;
  }, [turns]);

  return (
    <div className="chat-dock">
      <div className="chat-header">
        <span>artmind canvas</span>
        <button className="chat-collapse" onClick={onCollapse} aria-label="Collapse chat">
          ‹
        </button>
      </div>
      <div className="chat-log" ref={logRef} onScroll={onScroll}>
        {turns.length === 0 && (
          <div className="chat-hint">
            Ask a question — I'll open Cards as they help. Or spawn one directly:
            <ul className="chat-cmd-list">
              <li><code>/graph &lt;domain&gt; &lt;reference&gt;</code> — neighbourhood graph</li>
              <li><code>/provenance &lt;domain&gt; &lt;reference&gt;</code> — where a fact came from</li>
              <li><code>/document &lt;vaultPath&gt;</code> — open a Vault file</li>
              <li><code>/placement &lt;vaultPath&gt; [domain…]</code> — file a document</li>
            </ul>
          </div>
        )}
        {turns.map((turn, i) => {
          if (turn.role === "user") {
            return (
              <div key={i} className="chat-msg chat-msg-user">
                {turn.text}
              </div>
            );
          }
          const text = assistantText(turn);
          const canPromote = turn.done && !turn.error && text.length >= PROMOTE_MIN_CHARS;
          return (
            <div key={i} className="chat-msg chat-msg-assistant">
              {turn.blocks.length === 0 && !turn.error && !turn.done && (
                <span className="chat-cursor">…</span>
              )}
              {turn.blocks.map((b, j) =>
                b.kind === "thinking" ? (
                  <details key={j} className="chat-thinking" open={!b.done}>
                    <summary className={b.done ? "" : "chat-thinking-streaming"}>
                      {b.done ? "Thought" : "Thinking…"}
                    </summary>
                    <div className="chat-thinking-body">{b.text}</div>
                  </details>
                ) : (
                  <TextBlockView key={j} block={b} />
                ),
              )}
              {turn.tools.length > 0 && (
                <details className="chat-tools">
                  <summary>
                    {turn.tools.length} tool call{turn.tools.length > 1 ? "s" : ""}
                  </summary>
                  {turn.tools.map((tc, k) => (
                    <div key={k} className="chat-tool">
                      <div className="chat-tool-head">
                        <span className={`chat-tool-dot ${tc.result !== undefined ? "done" : "running"}`} />
                        <span className="chat-tool-name">{tc.name}</span>
                      </div>
                      {tc.input && <pre className="chat-tool-io">{tc.input}</pre>}
                      {tc.result !== undefined && (
                        <>
                          <div className="chat-tool-label">result</div>
                          <pre className="chat-tool-io">{tc.result}</pre>
                        </>
                      )}
                    </div>
                  ))}
                </details>
              )}
              {turn.error && <div className="chat-error">⚠️ {turn.error}</div>}
              {canPromote && (
                <button
                  className="chat-promote"
                  onClick={() => onPromote(text)}
                  title="Open this reply in the editor pane"
                >
                  ✎ Open in editor
                </button>
              )}
            </div>
          );
        })}
      </div>
      <div className="chat-input-row">
        <textarea
          ref={inputRef}
          className="chat-input"
          value={input}
          placeholder="Message artmind…"
          onChange={(e) => setInput(e.target.value)}
          onInput={onInput}
          onKeyDown={onKeyDown}
          rows={2}
        />
        <div className="chat-input-actions">
          {busy ? (
            <button className="chat-send chat-stop" onClick={() => void stop()}>
              Stop
            </button>
          ) : (
            <button className="chat-send" onClick={() => void send()}>
              Send
            </button>
          )}
          {input.trim().length >= PROMOTE_MIN_CHARS && (
            <button
              className="chat-to-editor"
              onClick={() => {
                onPromote(input);
                setInput("");
              }}
              title="Compose this in the editor pane instead"
            >
              ⤢ Editor
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

// The wire sometimes carries structured tool input/result (already clipped to a
// string by the backend's EventMapper, but be defensive).
function str(value: unknown): string | undefined {
  if (value === undefined || value === null) return undefined;
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}
