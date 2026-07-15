"use strict";

marked.use({ pedantic: false, gfm: true, breaks: false });

// ── page-level state ─────────────────────────────────────────────────
const sessionId = crypto.randomUUID();

const chatEl = document.getElementById("chat");
const promptEl = document.getElementById("prompt");
const composerEl = document.getElementById("composer");
const sendBtn = document.getElementById("send");
const iconSend = sendBtn.querySelector(".icon-send");
const iconStop = sendBtn.querySelector(".icon-stop");
const drawerEl = document.getElementById("drawer");
const traceListEl = document.getElementById("trace-list");
const traceBadgeEl = document.getElementById("trace-badge");

let streaming = false;
let abortController = null;
let toolCount = 0;

// per-turn rendering state
let turnEl = null;        // container for the current assistant turn
let textBlock = null;     // {el, raw}
let thinkingBlock = null; // {details, label, body, startedAt}

// ── helpers ──────────────────────────────────────────────────────────
function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

function nearBottom() {
  return window.innerHeight + window.scrollY >= document.body.scrollHeight - 120;
}

function scrollToBottom(force) {
  if (force || nearBottom()) window.scrollTo(0, document.body.scrollHeight);
}

// ── theme ────────────────────────────────────────────────────────────
document.getElementById("theme-toggle").addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  localStorage.setItem("artmind-theme", next);
});

// ── trace drawer ─────────────────────────────────────────────────────
const drawerCloseEl = document.getElementById("drawer-close");
drawerEl.inert = true;

function openDrawer() {
  document.body.classList.add("drawer-open");
  drawerEl.setAttribute("aria-hidden", "false");
  drawerEl.inert = false;
  drawerCloseEl.removeAttribute("tabindex");
}

function closeDrawer() {
  document.body.classList.remove("drawer-open");
  drawerEl.setAttribute("aria-hidden", "true");
  drawerEl.inert = true;
  drawerCloseEl.setAttribute("tabindex", "-1");
}

document.getElementById("trace-toggle").addEventListener("click", () => {
  if (document.body.classList.contains("drawer-open")) closeDrawer();
  else openDrawer();
});
document.getElementById("drawer-close").addEventListener("click", closeDrawer);

function bumpBadge() {
  toolCount += 1;
  traceBadgeEl.textContent = String(toolCount);
  traceBadgeEl.hidden = false;
}

function addToolCard(ev) {
  const card = el("div", "tool-card");
  card.dataset.toolId = ev.id;
  const head = el("div", "tool-head");
  const dot = el("span", "dot running");
  dot.title = "running";
  head.appendChild(dot);
  head.appendChild(el("span", "tool-name", ev.name));
  card.appendChild(head);
  const details = el("details");
  const summary = el("summary", null, ev.input);
  details.appendChild(summary);
  const pre = el("pre", null, ev.input);
  details.appendChild(pre);
  card.appendChild(details);
  traceListEl.appendChild(card);
  bumpBadge();
  traceListEl.scrollTop = traceListEl.scrollHeight;
}

function attachToolResult(ev) {
  const card = traceListEl.querySelector(`[data-tool-id="${CSS.escape(ev.tool_id)}"]`);
  if (!card) return;
  const dot = card.querySelector(".dot");
  dot.className = "dot done";
  dot.title = "done";
  card.appendChild(el("div", "tool-result-label", "result"));
  card.appendChild(el("pre", null, ev.content ?? ""));
  traceListEl.scrollTop = traceListEl.scrollHeight;
}

function addTraceSummary(ev) {
  const cost = ev.cost ? ` · $${ev.cost.toFixed(4)}` : "";
  traceListEl.appendChild(
    el("div", "trace-summary", `turn done · ${ev.turns} turns · ${ev.duration_s}s${cost}`)
  );
  traceListEl.scrollTop = traceListEl.scrollHeight;
}

// ── message rendering ────────────────────────────────────────────────
function addUserMessage(text) {
  const wrap = el("div", "msg-user");
  wrap.appendChild(el("div", "pill", text));
  chatEl.appendChild(wrap);
  scrollToBottom(true);
}

function ensureTurn() {
  if (!turnEl) {
    turnEl = el("div", "turn");
    chatEl.appendChild(turnEl);
  }
  return turnEl;
}

function ensureThinking() {
  if (thinkingBlock) return thinkingBlock;
  const details = el("details", "thinking");
  details.open = true;
  const summary = el("summary");
  const label = el("span", "thinking-label streaming", "Thinking");
  summary.appendChild(label);
  details.appendChild(summary);
  const body = el("div", "thinking-body");
  details.appendChild(body);
  ensureTurn().appendChild(details);
  thinkingBlock = { details, label, body, startedAt: Date.now() };
  return thinkingBlock;
}

function finalizeThinking() {
  if (!thinkingBlock) return;
  const secs = Math.max(1, Math.round((Date.now() - thinkingBlock.startedAt) / 1000));
  thinkingBlock.label.classList.remove("streaming");
  thinkingBlock.label.textContent = `Thought for ${secs}s`;
  thinkingBlock.details.open = false;
  thinkingBlock = null;
}

function ensureText() {
  if (textBlock) return textBlock;
  const node = el("div", "md streaming");
  ensureTurn().appendChild(node);
  textBlock = { el: node, raw: "" };
  return textBlock;
}

function finalizeText() {
  if (!textBlock) return;
  textBlock.el.classList.remove("streaming");
  textBlock.el.innerHTML = DOMPurify.sanitize(marked.parse(textBlock.raw));
  addCopyButtons(textBlock.el);
  textBlock = null;
}

function addCopyButtons(scope) {
  for (const pre of scope.querySelectorAll("pre")) {
    const btn = el("button", "copy-btn", "copy");
    btn.type = "button";
    btn.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(pre.querySelector("code")?.textContent ?? pre.textContent);
        btn.textContent = "copied";
      } catch (err) {
        console.error("copy failed", err);
        btn.textContent = "failed";
      }
      setTimeout(() => (btn.textContent = "copy"), 1200);
    });
    pre.appendChild(btn);
  }
}

function showNotice(text) {
  chatEl.appendChild(el("div", "notice", text));
  scrollToBottom(true);
}

// ── event dispatch ───────────────────────────────────────────────────
function handleEvent(ev) {
  switch (ev.type) {
    case "thinking_delta":
      ensureThinking().body.textContent += ev.text;
      break;
    case "text_delta":
      finalizeThinking(); // answer started: collapse live thinking
      ensureText();
      textBlock.raw += ev.text;
      textBlock.el.textContent = textBlock.raw;
      break;
    case "block_done":
      if (ev.block === "text") finalizeText();
      else finalizeThinking();
      break;
    case "tool_call":
      addToolCard(ev);
      break;
    case "tool_result":
      attachToolResult(ev);
      break;
    case "turn_done":
      addTraceSummary(ev);
      break;
    case "error":
      showNotice(`Agent error: ${ev.message}`);
      const card = el("div", "tool-card");
      const head = el("div", "tool-head");
      const dot = el("span", "dot error");
      dot.title = "error";
      head.appendChild(dot);
      head.appendChild(el("span", "tool-name", "Error"));
      card.appendChild(head);
      card.appendChild(el("pre", null, ev.message));
      traceListEl.appendChild(card);
      break;
  }
  scrollToBottom(false);
}

// ── streaming transport ──────────────────────────────────────────────
async function streamTurn(prompt) {
  abortController = new AbortController();
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, prompt }),
    signal: abortController.signal,
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split("\n\n");
    buffer = frames.pop(); // keep the trailing partial frame
    for (const frame of frames) {
      const line = frame.trim();
      if (line.startsWith("data: ")) handleEvent(JSON.parse(line.slice(6)));
    }
  }
}

function setStreaming(on) {
  streaming = on;
  iconSend.hidden = on;
  iconStop.hidden = !on;
  sendBtn.disabled = on ? false : promptEl.value.trim() === "";
  sendBtn.setAttribute("aria-label", on ? "Stop" : "Send");
}

async function send() {
  const prompt = promptEl.value.trim();
  if (!prompt || streaming) return;
  promptEl.value = "";
  autogrow();
  addUserMessage(prompt);
  setStreaming(true);
  try {
    await streamTurn(prompt);
  } catch (err) {
    if (err.name !== "AbortError") {
      console.error(err);
      showNotice("Connection lost — send again to retry.");
    }
  } finally {
    finalizeThinking();
    finalizeText();
    turnEl = null;
    setStreaming(false);
    promptEl.focus();
  }
}

async function stop() {
  if (abortController) abortController.abort();
  try {
    await fetch(`/api/session/${sessionId}/interrupt`, { method: "POST" });
  } catch (_) { /* server may already be gone */ }
}

// ── composer wiring ──────────────────────────────────────────────────
function autogrow() {
  promptEl.style.height = "auto";
  promptEl.style.height = Math.min(promptEl.scrollHeight, 200) + "px";
}

promptEl.addEventListener("input", () => {
  autogrow();
  if (!streaming) sendBtn.disabled = promptEl.value.trim() === "";
});

promptEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    send();
  }
});

composerEl.addEventListener("submit", (e) => {
  e.preventDefault();
  if (streaming) stop();
  else send();
});

// ── cleanup on tab close ─────────────────────────────────────────────
window.addEventListener("pagehide", () => {
  fetch(`/api/session/${sessionId}`, { method: "DELETE", keepalive: true }).catch(() => {});
});
