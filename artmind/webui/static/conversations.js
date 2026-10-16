"use strict";

// Conversation sidebar and server-side history (spec 2026-10-16 §5).
// Loaded after app.js: it shares app.js's top-level bindings, and the URL
// (/?c=<id>) is the state, so reload, back/forward and Obsidian Web Viewer
// round trips all come back to the same conversation.
(() => {
  const sidebarEl = document.getElementById("sidebar");
  const listEl = document.getElementById("conv-list");
  const moreEl = document.getElementById("conv-more");
  const filterEl = document.getElementById("conv-filter");
  const archivedToggleEl = document.getElementById("conv-archived-toggle");
  const toggleEl = document.getElementById("sidebar-toggle");
  const newChatEl = document.getElementById("new-chat");

  const SIDEBAR_KEY = "artmind-sidebar";
  const WIDE = window.matchMedia("(min-width: 900px)");
  const GROUPS = ["Pinned", "Today", "Yesterday", "Previous 7 days", "Earlier"];
  const TITLE_MAX = 60;

  let items = [];
  let nextCursor = null;
  let showArchived = false;
  let filterText = "";

  // ── api ────────────────────────────────────────────────────────────
  async function api(path, options) {
    const response = await fetch(path, options);
    if (!response.ok) {
      const err = new Error(`HTTP ${response.status}`);
      err.status = response.status;
      throw err;
    }
    return response.json();
  }

  const JSON_HEADERS = { "Content-Type": "application/json" };

  // ── sidebar open/closed ────────────────────────────────────────────
  function setSidebar(open, persist = true) {
    document.body.classList.toggle("sidebar-open", open);
    sidebarEl.inert = !open;
    sidebarEl.setAttribute("aria-hidden", String(!open));
    if (!persist) return;
    try {
      localStorage.setItem(SIDEBAR_KEY, open ? "open" : "closed");
    } catch (_) { /* a per-viewer convenience only */ }
  }

  function initialSidebarState() {
    try {
      const saved = localStorage.getItem(SIDEBAR_KEY);
      if (saved) return saved === "open";
    } catch (_) { /* fall through to the default */ }
    return WIDE.matches;
  }

  function closeSidebarIfOverlay() {
    if (!WIDE.matches) setSidebar(false, false);
  }

  toggleEl.addEventListener("click", () => {
    setSidebar(!document.body.classList.contains("sidebar-open"));
  });

  // ── list rendering ─────────────────────────────────────────────────
  function makeTitle(text) {
    const oneLine = text.split(/\s+/).filter(Boolean).join(" ");
    if (!oneLine) return "New conversation";
    return oneLine.length <= TITLE_MAX ? oneLine : oneLine.slice(0, TITLE_MAX - 1).trimEnd() + "…";
  }

  function groupOf(c, now) {
    if (c.pinned) return "Pinned";
    const startOfToday = new Date(now).setHours(0, 0, 0, 0);
    const t = c.updated_at * 1000;
    if (t >= startOfToday) return "Today";
    if (t >= startOfToday - 86400000) return "Yesterday";
    if (t >= startOfToday - 7 * 86400000) return "Previous 7 days";
    return "Earlier";
  }

  function renderList() {
    listEl.replaceChildren();
    const needle = filterText.trim().toLowerCase();
    const shown = items.filter((c) => !needle || (c.title || "").toLowerCase().includes(needle));
    if (shown.length === 0) {
      const text = items.length ? "No matches." : showArchived ? "No archived conversations." : "No conversations yet.";
      listEl.appendChild(el("div", "conv-empty", text));
    }
    const now = Date.now();
    for (const group of GROUPS) {
      const rows = shown.filter((c) => groupOf(c, now) === group);
      if (rows.length === 0) continue;
      listEl.appendChild(el("div", "conv-group", group));
      for (const c of rows) listEl.appendChild(rowFor(c));
    }
    moreEl.hidden = nextCursor === null;
    archivedToggleEl.textContent = showArchived ? "Back to conversations" : "Archived";
  }

  function rowFor(c) {
    const row = el("div", "conv-row");
    row.dataset.id = c.conversation_id;
    if (c.conversation_id === conversationId) row.classList.add("active");

    const open = el("button", "conv-title", c.title || "(untitled)");
    open.type = "button";
    open.title = c.title || "";
    open.addEventListener("click", () => openConversation(c.conversation_id));
    row.appendChild(open);

    const menu = el("details", "conv-menu");
    menu.appendChild(el("summary", null, "⋯"));
    const panel = el("div", "conv-menu-panel");
    const actions = [
      [c.pinned ? "Unpin" : "Pin", () => patch(c, { pinned: !c.pinned })],
      ["Rename", () => startRename(row, c)],
      [c.archived_at ? "Unarchive" : "Archive", () => patch(c, { archived: !c.archived_at })],
    ];
    for (const [label, run] of actions) {
      const button = el("button", null, label);
      button.type = "button";
      button.addEventListener("click", () => {
        menu.open = false;
        run();
      });
      panel.appendChild(button);
    }
    panel.appendChild(deleteButton(menu, c));
    menu.appendChild(panel);
    row.appendChild(menu);
    return row;
  }

  // Two-step delete: the first click arms it for 3 s. No window.confirm --
  // embedded webviews (Obsidian's Web Viewer) may suppress native dialogs.
  function deleteButton(menu, c) {
    const button = el("button", "danger", "Delete");
    button.type = "button";
    let armed = null;
    button.addEventListener("click", () => {
      if (armed === null) {
        button.textContent = "Confirm delete";
        armed = setTimeout(() => {
          armed = null;
          button.textContent = "Delete";
        }, 3000);
        return;
      }
      clearTimeout(armed);
      menu.open = false;
      remove(c);
    });
    return button;
  }

  function startRename(row, c) {
    const titleButton = row.querySelector(".conv-title");
    const input = el("input", "conv-rename");
    input.type = "text";
    input.value = c.title || "";
    input.maxLength = 200;
    titleButton.replaceWith(input);
    input.focus();
    input.select();
    let finished = false;
    const finish = async (commit) => {
      if (finished) return;
      finished = true;
      const value = input.value.trim();
      if (commit && value && value !== c.title) await patch(c, { title: value });
      else renderList();
    };
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") finish(true);
      else if (e.key === "Escape") finish(false);
    });
    input.addEventListener("blur", () => finish(true));
  }

  // ── list data ──────────────────────────────────────────────────────
  async function loadList(reset) {
    if (reset) {
      items = [];
      nextCursor = null;
    }
    const query = new URLSearchParams({ limit: "30" });
    if (showArchived) query.set("archived", "true");
    if (nextCursor) query.set("cursor", nextCursor);
    try {
      const data = await api(`/api/conversations?${query}`);
      items = items.concat(data.conversations);
      nextCursor = data.next_cursor;
    } catch (err) {
      console.error("conversation list failed", err);
      showNotice(`Couldn't load conversations: ${err.message}`);
    }
    renderList();
  }

  async function patch(c, body) {
    try {
      await api(`/api/conversations/${encodeURIComponent(c.conversation_id)}`, {
        method: "PATCH",
        headers: JSON_HEADERS,
        body: JSON.stringify(body),
      });
    } catch (err) {
      showNotice(`Couldn't update the conversation: ${err.message}`);
      return;
    }
    await loadList(true);
  }

  async function remove(c) {
    try {
      await api(`/api/conversations/${encodeURIComponent(c.conversation_id)}`, { method: "DELETE" });
    } catch (err) {
      showNotice(
        err.status === 409
          ? "That conversation is still answering; stop it first."
          : `Couldn't delete the conversation: ${err.message}`
      );
      return;
    }
    if (c.conversation_id === conversationId) newConversation(true);
    await loadList(true);
  }

  filterEl.addEventListener("input", () => {
    filterText = filterEl.value;
    renderList();
  });
  moreEl.addEventListener("click", () => loadList(false));
  archivedToggleEl.addEventListener("click", () => {
    showArchived = !showArchived;
    loadList(true);
  });

  // ── view reset and stored-turn rendering ───────────────────────────
  function resetView() {
    for (const node of [...chatEl.children]) {
      if (node.id !== "suggested-chips") node.remove();
    }
    traceListEl.replaceChildren();
    toolCount = 0;
    traceBadgeEl.textContent = "0";
    traceBadgeEl.hidden = true;
    turnEl = null;
    textBlock = null;
    thinkingBlock = null;
    traceId = null;
  }

  function renderThinking(block) {
    const details = el("details", "thinking");
    const summary = el("summary");
    summary.appendChild(el("span", "thinking-label", "Thought"));
    details.appendChild(summary);
    details.appendChild(el("div", "thinking-body", block.md));
    return details;
  }

  function renderText(block) {
    const node = el("div", "md");
    node.innerHTML = DOMPurify.sanitize(marked.parse(block.md));
    addCopyButtons(node);
    linkVaultSources(node);
    return node;
  }

  function renderTurns(turns) {
    let previousBackend = null;
    for (const t of turns) {
      if (previousBackend && t.backend !== previousBackend) {
        chatEl.appendChild(
          el("div", "conversation-divider", `Continued on ${BACKEND_LABELS[t.backend] ?? t.backend}`)
        );
      }
      previousBackend = t.backend;

      const user = el("div", "msg-user");
      user.appendChild(el("div", "pill", t.user_text));
      chatEl.appendChild(user);

      const turn = el("div", "turn");
      for (const block of t.blocks) {
        if (block.type === "thinking") turn.appendChild(renderThinking(block));
        else if (block.type === "text") turn.appendChild(renderText(block));
        else if (block.type === "tool") {
          addToolCard({ id: block.id, name: block.name, input: block.input ?? "" });
          if (block.result !== null && block.result !== undefined) {
            attachToolResult({ tool_id: block.id, content: block.result });
          }
        }
      }
      if (t.status === "interrupted") {
        turn.appendChild(el("div", "turn-status", "Interrupted: this turn did not finish."));
      } else if (t.status === "error") {
        turn.appendChild(el("div", "turn-status error", `Error: ${t.error_message ?? "the turn failed"}`));
      } else if (t.status === "running") {
        turn.appendChild(el("div", "turn-status", "Still running, or interrupted."));
      }
      if (t.trace_id) turn.appendChild(feedbackControl(t.trace_id));
      chatEl.appendChild(turn);
    }
  }

  // ── opening and starting conversations ─────────────────────────────
  function currentIdFromUrl() {
    return new URLSearchParams(window.location.search).get("c");
  }

  function newConversation(push) {
    if (streaming) return;
    resetView();
    conversationId = crypto.randomUUID();
    if (push) history.pushState({}, "", "/");
    renderList();
    promptEl.focus();
  }

  async function openConversation(id, push = true) {
    if (streaming) return;
    let data;
    try {
      data = await api(`/api/conversations/${encodeURIComponent(id)}`);
    } catch (err) {
      newConversation(false);
      history.replaceState({}, "", "/");
      showNotice(err.status === 404 ? "Conversation not found. Started a new chat." : `Couldn't load it: ${err.message}`);
      return;
    }
    resetView();
    conversationId = id;
    const last = data.turns[data.turns.length - 1];
    if (last && last.backend !== backend) {
      backend = last.backend;
      for (const radio of backendRadios) radio.checked = radio.value === backend;
    }
    renderTurns(data.turns);
    if (push) history.pushState({ c: id }, "", `/?c=${encodeURIComponent(id)}`);
    renderList();
    scrollToBottom(true);
    closeSidebarIfOverlay();
  }

  newChatEl.addEventListener("click", () => {
    newConversation(true);
    closeSidebarIfOverlay();
  });

  window.addEventListener("popstate", () => {
    if (streaming) return;
    const id = currentIdFromUrl();
    if (id) openConversation(id, false);
    else newConversation(false);
  });

  // ── follow the live conversation ───────────────────────────────────
  chatHooks.onTurnStart = (id, prompt) => {
    if (currentIdFromUrl() !== id) history.replaceState({ c: id }, "", `/?c=${encodeURIComponent(id)}`);
    if (showArchived) return;
    const at = items.findIndex((c) => c.conversation_id === id);
    const c = at >= 0
      ? items.splice(at, 1)[0]
      : { conversation_id: id, title: makeTitle(prompt), pinned: 0, archived_at: null };
    c.updated_at = Date.now() / 1000;
    items.unshift(c);
    renderList();
  };

  // ── start ──────────────────────────────────────────────────────────
  setSidebar(initialSidebarState(), false);
  loadList(true);
  const initialId = currentIdFromUrl();
  if (initialId) openConversation(initialId, false);
})();
