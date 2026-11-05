"use strict";

// Mermaid diagrams in rendered markdown (chat-ui, admin-ui chat, admin-ui Views tab).
//
// marked turns a ```mermaid fence into <pre><code class="language-mermaid">; this
// replaces each such block with the rendered SVG. The 5 MB library is vendored
// (vendor/mermaid.min.js) and loaded on the first diagram only, so pages that never
// show one pay nothing. Mermaid runs with securityLevel "strict" (it sanitizes the
// diagram text and its SVG itself), after DOMPurify has already cleaned the markdown.
// A diagram that fails to parse stays a plain code block with a one-line note.
// Call renderMermaid(scope) after a node's markdown is in the DOM; it is idempotent.
(function () {
  const SRC = document.currentScript && document.currentScript.dataset.mermaidSrc;
  let loading = null;
  let counter = 0;

  function theme() {
    return document.documentElement.dataset.theme === "dark" ? "dark" : "default";
  }

  function load() {
    if (window.mermaid) return Promise.resolve(window.mermaid);
    if (!loading) {
      loading = new Promise((resolve, reject) => {
        const s = document.createElement("script");
        s.src = SRC;
        s.onload = () => resolve(window.mermaid);
        s.onerror = () => { loading = null; reject(new Error("mermaid failed to load")); };
        document.head.appendChild(s);
      });
    }
    return loading;
  }

  function note(host, text) {
    const n = document.createElement("div");
    n.className = "mermaid-error";
    n.textContent = text;
    host.insertBefore(n, host.firstChild);
  }

  async function draw(host) {
    const mermaid = await load();
    mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme: theme() });
    const id = `mermaid-${++counter}`;
    let svg;
    try {
      ({ svg } = await mermaid.render(id, host._mermaidSource));
    } catch (err) {
      document.getElementById(`d${id}`)?.remove(); // mermaid leaves its error graphic in <body>
      throw err;
    }
    host.classList.remove("mermaid-failed");
    host.innerHTML = svg;
    host.dataset.theme = theme();
  }

  window.renderMermaid = function (scope) {
    for (const code of scope.querySelectorAll("pre > code.language-mermaid")) {
      const pre = code.parentElement;
      const host = document.createElement("div");
      host.className = "mermaid-diagram";
      host._mermaidSource = code.textContent;
      pre.replaceWith(host);
      draw(host).catch((err) => {
        // Put the source back as a code block so nothing is lost.
        host.classList.add("mermaid-failed");
        host.replaceChildren();
        const p = document.createElement("pre");
        const c = document.createElement("code");
        c.textContent = host._mermaidSource;
        p.appendChild(c);
        host.appendChild(p);
        note(host, "Diagram could not be rendered" + (err && err.message ? `: ${String(err.message).split("\n")[0]}` : "."));
        if (typeof addCopyButtons === "function") addCopyButtons(host);
      });
    }
  };

  // Re-draw rendered diagrams when the page theme is toggled.
  new MutationObserver(() => {
    for (const host of document.querySelectorAll(".mermaid-diagram:not(.mermaid-failed)")) {
      if (host.dataset.theme !== theme()) draw(host).catch(() => {});
    }
  }).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
})();
