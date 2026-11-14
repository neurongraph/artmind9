"use strict";

// Mermaid diagrams in rendered markdown (chat-ui, admin-ui chat, admin-ui Views tab).
//
// marked turns a ```mermaid fence into <pre><code class="language-mermaid">; this
// replaces each such block with the rendered SVG. The 5 MB library is vendored
// (vendor/mermaid.min.js) and loaded on the first diagram only, so pages that never
// show one pay nothing. Mermaid runs with securityLevel "strict" (it sanitizes the
// diagram text and its SVG itself), after DOMPurify has already cleaned the markdown.
// A diagram that fails to parse stays a plain code block with a one-line note.
// A rendered diagram has a hover toolbar: "expand" opens it full-window (the shared
// viewer in viewer.js) with
// wheel zoom and drag pan (a wide timeline is unreadable at column width), and
// "copy source" puts the mermaid text on the clipboard.
// Call renderMermaid(scope) after a node's markdown is in the DOM; it is idempotent.
(function () {
  const SRC = document.currentScript && document.currentScript.dataset.mermaidSrc;
  let loading = null;
  let counter = 0;
  const button = (label, title, onClick) => window.ArtmindViewer.button(label, title, onClick);

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
    host.appendChild(toolbar(host));
  }

  function toolbar(host) {
    const bar = document.createElement("div");
    bar.className = "mermaid-toolbar";
    bar.appendChild(button("expand", "Open full window (zoom and pan)", () => expand(host)));
    const copy = button("copy source", "Copy the mermaid source", async () => {
      try {
        await navigator.clipboard.writeText(host._mermaidSource);
        copy.textContent = "copied";
      } catch (err) {
        console.error("copy failed", err);
        copy.textContent = "failed";
      }
      setTimeout(() => (copy.textContent = "copy source"), 1200);
    });
    bar.appendChild(copy);
    return bar;
  }

  // ── full-window viewer ──────────────────────────────────────────────
  const MIN_SCALE = 0.05;
  const MAX_SCALE = 8;

  function expand(host) {
    const source = host.querySelector("svg");
    if (!source) return;
    const svg = source.cloneNode(true);
    // Mermaid sizes its svg to 100% of the column; give it its natural size and
    // scale the whole stage instead.
    const box = source.viewBox && source.viewBox.baseVal;
    const natural = box && box.width ? { w: box.width, h: box.height } : { w: source.getBBox().width, h: source.getBBox().height };
    svg.removeAttribute("style");
    svg.setAttribute("width", natural.w);
    svg.setAttribute("height", natural.h);

    let scale = 1, x = 0, y = 0;
    const stage = document.createElement("div");
    stage.className = "viewer-stage";
    stage.appendChild(svg);
    const apply = () => { stage.style.transform = `translate(${x}px, ${y}px) scale(${scale})`; };
    const zoomAt = (factor, cx, cy) => {
      const next = Math.min(MAX_SCALE, Math.max(MIN_SCALE, scale * factor));
      const k = next / scale;
      x = cx - (cx - x) * k;
      y = cy - (cy - y) * k;
      scale = next;
      apply();
    };
    let view = null;
    const fit = () => {
      const vw = view.overlay.clientWidth, vh = view.overlay.clientHeight;
      scale = Math.min((vw * 0.94) / natural.w, (vh * 0.86) / natural.h, 4);
      x = (vw - natural.w * scale) / 2;
      y = (vh - natural.h * scale) / 2;
      apply();
    };
    const zoomCentre = (factor) => zoomAt(factor, view.overlay.clientWidth / 2, view.overlay.clientHeight / 2);

    view = window.ArtmindViewer.open({
      label: "Diagram",
      className: "pan",
      buttons: [
        ["\u2212", "Zoom out", () => zoomCentre(1 / 1.25)],
        ["+", "Zoom in", () => zoomCentre(1.25)],
        ["fit", "Fit to window", fit],
        ["100%", "Actual size", () => zoomCentre(1 / scale)],
      ],
      keys: { "+": () => zoomCentre(1.25), "=": () => zoomCentre(1.25), "-": () => zoomCentre(1 / 1.25), "0": fit },
      onResize: fit,
    });
    const overlay = view.overlay;
    overlay.insertBefore(stage, view.bar);

    // Pointer drag pans; a press that never moved on the backdrop closes.
    let drag = null;
    overlay.addEventListener("pointerdown", (e) => {
      if (e.target.closest(".viewer-bar")) return;
      drag = { px: e.clientX, py: e.clientY, x, y, moved: false, onDiagram: !!e.target.closest("svg") };
      overlay.setPointerCapture(e.pointerId);
      overlay.classList.add("panning");
    });
    overlay.addEventListener("pointermove", (e) => {
      if (!drag) return;
      const dx = e.clientX - drag.px, dy = e.clientY - drag.py;
      if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
      x = drag.x + dx;
      y = drag.y + dy;
      apply();
    });
    overlay.addEventListener("pointerup", () => {
      if (drag && !drag.moved && !drag.onDiagram) view.close();
      drag = null;
      overlay.classList.remove("panning");
    });
    overlay.addEventListener("pointercancel", () => { drag = null; overlay.classList.remove("panning"); });
    overlay.addEventListener("wheel", (e) => {
      e.preventDefault();
      zoomAt(Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0015)), e.clientX, e.clientY);
    }, { passive: false });
    overlay.addEventListener("dblclick", (e) => { if (!e.target.closest(".viewer-bar")) fit(); });
    fit();
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
