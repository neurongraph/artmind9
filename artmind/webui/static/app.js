"use strict";

marked.use({ pedantic: false, gfm: true, breaks: false });

// ── page-level state ─────────────────────────────────────────────────
let conversationId = crypto.randomUUID();
let backend = "claude-sdk";

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
let traceId = null;       // admin console: this turn's query-trace id

// Filled in by conversations.js (loaded after this file): lets the sidebar
// follow a conversation as its first turn starts.
const chatHooks = { onTurnStart(_conversationId, _prompt) {}, onTurnEnd(_conversationId) {} };

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

function updateToolCard(ev) {
  const card = traceListEl.querySelector(`[data-tool-id="${CSS.escape(ev.id)}"]`);
  if (!card) return;
  card.querySelector(".tool-name").textContent = ev.name;
  const details = card.querySelector("details");
  details.querySelector("summary").textContent = ev.input;
  details.querySelector("pre").textContent = ev.input;
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
  linkVaultSources(textBlock.el);
  textBlock = null;
}

// ── vault source links ───────────────────────────────────────────────
// Answers end with a Sources list linking each document's vault path
// (artmind-query skill). Each such link gets its obsidian:// URI as href, so
// a plain browser click and Web Viewer's right-click "Open link in default
// browser" both work; a click is sent to the server instead, because Web
// Viewer drops a page's obsidian:// navigation (see webui/source_links.py).
let vaultDir = null;
fetch("/api/health")
  .then((r) => r.json())
  .then((h) => {
    vaultDir = h.vault;
    linkVaultSources(chatEl); // links anything rendered before this resolved, restored history included
  })
  .catch(() => {});

function vaultRelative(href) {
  let path;
  try {
    path = decodeURIComponent(href);
  } catch (_) {
    return null;
  }
  if (/^[a-z][a-z0-9+.-]*:/i.test(path) || /^[#?]/.test(path)) return null;
  if (path.startsWith("/")) {
    if (!path.startsWith(vaultDir + "/")) return null;
    path = path.slice(vaultDir.length + 1);
  }
  path = path.replace(/^\.\//, "");
  if (!path || path.split("/").includes("..")) return null;
  return path;
}

function obsidianUri(rel) {
  const vaultName = vaultDir.split("/").pop();
  const file = rel.endsWith(".md") ? rel.slice(0, -3) : rel;
  // paneType=tab: a new Obsidian tab, never the Web Viewer's own (see source_links.py).
  return `obsidian://open?vault=${encodeURIComponent(vaultName)}&file=${encodeURIComponent(file)}&paneType=tab`;
}

function linkVaultSources(scope) {
  if (!vaultDir) return;
  for (const a of scope.querySelectorAll("a[href]")) {
    const rel = vaultRelative(a.getAttribute("href"));
    if (!rel) continue;
    a.dataset.vaultPath = rel;
    a.href = obsidianUri(rel);
    a.classList.add("vault-link");
    a.title = `Open ${rel} in Obsidian`;
  }
}

// Copy buttons and vault-source links are handled by one delegated listener
// here, rather than a listener attached to each element, because a stored
// conversation is rendered by conversations.js after the fact -- one listener
// on #chat covers turns however they got there.
chatEl.addEventListener("click", async (e) => {
  const copyBtn = e.target.closest(".copy-btn");
  if (copyBtn) {
    const pre = copyBtn.closest("pre");
    try {
      await navigator.clipboard.writeText(pre.querySelector("code")?.textContent ?? pre.textContent);
      copyBtn.textContent = "copied";
    } catch (err) {
      console.error("copy failed", err);
      copyBtn.textContent = "failed";
    }
    setTimeout(() => (copyBtn.textContent = "copy"), 1200);
    return;
  }
  const a = e.target.closest("a[data-vault-path]");
  if (!a) return;
  e.preventDefault();
  try {
    const r = await fetch("/api/open-source", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: a.dataset.vaultPath }),
    });
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      showNotice(`Couldn't open ${a.dataset.vaultPath}: ${body.detail ?? `HTTP ${r.status}`}`);
    }
  } catch (err) {
    console.error("open-source failed", err);
    window.location.href = a.href; // server unreachable: let the browser try
  }
});

function addCopyButtons(scope) {
  for (const pre of scope.querySelectorAll("pre")) {
    if (pre.querySelector(".copy-btn")) continue; // idempotent: restored HTML already has one
    const btn = el("button", "copy-btn", "copy");
    btn.type = "button";
    pre.appendChild(btn);
  }
}

function showNotice(text, cls = "notice") {
  chatEl.appendChild(el("div", cls, text));
  scrollToBottom(true);
}

// ── answer feedback (admin console) ─────────────────────────────────
// Rates one traced turn (spec 2026-10-11 §5.7). Rendered only when the
// server sent a `trace` event; handlers are delegated on #chat because a
// restored turns are rendered later by conversations.js.
function feedbackControl(id) {
  const wrap = el("div", "feedback");
  wrap.dataset.traceId = id;
  for (const [rating, label] of [["good", "Good"], ["partial", "Partial"], ["wrong", "Wrong"]]) {
    const btn = el("button", "feedback-btn", label);
    btn.type = "button";
    btn.dataset.rating = rating;
    wrap.appendChild(btn);
  }
  const note = el("textarea", "feedback-note");
  note.placeholder = "What was expected? (optional)";
  note.rows = 2;
  note.hidden = true;
  wrap.appendChild(note);
  const save = el("button", "feedback-save", "Save note");
  save.type = "button";
  save.hidden = true;
  wrap.appendChild(save);
  wrap.appendChild(el("span", "feedback-status"));
  return wrap;
}

async function postFeedback(wrap, rating) {
  const status = wrap.querySelector(".feedback-status");
  const note = wrap.querySelector(".feedback-note");
  try {
    const r = await fetch(`/api/traces/${wrap.dataset.traceId}/feedback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rating, expected: note.value.trim() || null }),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    wrap.dataset.rating = rating;
    for (const b of wrap.querySelectorAll(".feedback-btn")) {
      b.classList.toggle("selected", b.dataset.rating === rating);
    }
    status.textContent = "Saved";
  } catch (err) {
    status.textContent = `Not saved: ${err.message}`;
  }
}

chatEl.addEventListener("click", (e) => {
  const wrap = e.target.closest(".feedback");
  if (!wrap) return;
  const btn = e.target.closest(".feedback-btn");
  if (btn) {
    const note = wrap.querySelector(".feedback-note");
    const save = wrap.querySelector(".feedback-save");
    const wantsNote = btn.dataset.rating !== "good";
    note.hidden = !wantsNote;
    save.hidden = !wantsNote;
    postFeedback(wrap, btn.dataset.rating);
    if (wantsNote) note.focus();
    return;
  }
  if (e.target.closest(".feedback-save") && wrap.dataset.rating) {
    postFeedback(wrap, wrap.dataset.rating);
  }
});

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
    case "tool_call_update":
      updateToolCard(ev);
      break;
    case "tool_result":
      attachToolResult(ev);
      break;
    case "context_restored":
      showNotice(
        `Context restored from ${ev.turns} earlier turn${ev.turns === 1 ? "" : "s"}` +
          (ev.omitted ? ` (${ev.omitted} older omitted)` : "") + ".",
        "conversation-divider"
      );
      break;
    case "trace":
      traceId = ev.trace_id;
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
    body: JSON.stringify({ conversation_id: conversationId, prompt, backend }),
    signal: abortController.signal,
  });
  if (!response.ok) {
    const err = new Error(`HTTP ${response.status}`);
    err.status = response.status;
    throw err;
  }

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
  // toggleAttribute, not .hidden: these are SVG elements, which have no
  // hidden property — the CSS [hidden] rule does the actual hiding
  iconSend.toggleAttribute("hidden", on);
  iconStop.toggleAttribute("hidden", !on);
  sendBtn.disabled = on ? false : promptEl.value.trim() === "";
  sendBtn.setAttribute("aria-label", on ? "Stop" : "Send");
  for (const radio of backendRadios) radio.disabled = on;
}

async function send() {
  const prompt = promptEl.value.trim();
  if (!prompt || streaming) return;
  promptEl.value = "";
  autogrow();
  addUserMessage(prompt);
  chatHooks.onTurnStart(conversationId, prompt);
  setStreaming(true);
  try {
    await streamTurn(prompt);
  } catch (err) {
    if (err.name !== "AbortError") {
      console.error(err);
      showNotice(
        err.status === 409
          ? "This conversation is answering in another tab. Wait for it to finish, then send again."
          : "Connection lost — send again to retry."
      );
    }
  } finally {
    finalizeThinking();
    finalizeText();
    if (turnEl && traceId) turnEl.appendChild(feedbackControl(traceId));
    traceId = null;
    turnEl = null;
    chatHooks.onTurnEnd(conversationId);
    setStreaming(false);
    promptEl.focus();
  }
}

async function stop() {
  if (abortController) abortController.abort();
  try {
    await fetch(`/api/session/${conversationId}/interrupt`, { method: "POST" });
  } catch (_) { /* server may already be gone */ }
}

// ── backend picker ───────────────────────────────────────────────────
// History is stored server-side, so switching backends keeps the conversation:
// the next send carries the new backend and the server replays the stored
// transcript into the fresh agent (spec 2026-10-16 §4.1).
const backendRadios = document.querySelectorAll('input[name="backend"]');
const BACKEND_LABELS = { "claude-sdk": "Claude (SDK)", "acp": "opencode (ACP)" };

for (const radio of backendRadios) {
  radio.addEventListener("change", () => {
    if (!radio.checked || radio.value === backend) return;
    backend = radio.value;
    if (chatEl.querySelector(".msg-user")) {
      chatEl.appendChild(el("div", "conversation-divider", `Continued on ${BACKEND_LABELS[backend]}`));
      scrollToBottom(true);
    }
    promptEl.focus();
  });
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

// A deep link (`/?prompt=…`, the Obsidian plugin's table-review row) arrives
// with the composer already filled server-side. Never sent: the operator
// reads it, edits it if they want, and presses Enter.
if (promptEl.value.trim() !== "") {
  autogrow();
  sendBtn.disabled = false;
  promptEl.setSelectionRange(promptEl.value.length, promptEl.value.length);
}

// `/?ask=…` (the admin console's Insights cards) prefills the composer
// like `?prompt=` does, but is applied client-side. Never sent.
(function applyAskParam() {
  const params = new URLSearchParams(window.location.search);
  const ask = params.get("ask");
  if (!ask) return;
  promptEl.value = ask;
  autogrow();
  sendBtn.disabled = ask.trim() === "";
  promptEl.focus();
  promptEl.setSelectionRange(promptEl.value.length, promptEl.value.length);
  params.delete("ask");
  const qs = params.toString();
  history.replaceState(history.state, "", window.location.pathname + (qs ? "?" + qs : "") + window.location.hash);
})();

composerEl.addEventListener("submit", (e) => {
  e.preventDefault();
  if (streaming) stop();
  else send();
});

// ── release the live agent on tab close (the stored conversation is kept) ──
window.addEventListener("pagehide", () => {
  fetch(`/api/session/${conversationId}`, { method: "DELETE", keepalive: true }).catch(() => {});
});
