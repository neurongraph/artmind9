"use strict";

// Admin chat: a "View as graph" link under each of the operator's messages.
// It opens Explore -> Graph with that message as the question. Delegated by
// observing #chat, so turns restored later by conversations.js get it too.
// Admin console only (admin.html); chat-ui does not load this file.

(function () {
  const chat = document.getElementById("chat");
  if (!chat) return;

  function decorate(wrap) {
    if (wrap.querySelector(".graph-link")) return; // idempotent
    const pill = wrap.querySelector(".pill");
    if (!pill || !pill.textContent.trim()) return;
    const link = document.createElement("a");
    link.className = "graph-link";
    link.textContent = "View as graph";
    link.title = "Turn this question into Cypher and open it in Neo4j Browser";
    link.target = "_blank";
    link.rel = "noopener";
    link.href = "/dashboard?tab=graph&q=" + encodeURIComponent(pill.textContent.trim().slice(0, 2000));
    wrap.appendChild(link);
  }

  function scan(root) {
    if (root.nodeType !== 1) return;
    if (root.classList.contains("msg-user")) decorate(root);
    for (const w of root.querySelectorAll(".msg-user")) decorate(w);
  }

  scan(chat);
  new MutationObserver((records) => {
    for (const r of records) for (const n of r.addedNodes) scan(n);
  }).observe(chat, { childList: true, subtree: true });
})();
