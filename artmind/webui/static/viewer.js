"use strict";

// The full-window viewer shared by mermaid-render.js and graph-render.js: a dialog
// overlay with a button bar. Esc closes it, and focus goes back to the opener.
// Callers put their own content (and zoom) in `overlay`; the bar stays on top.
//   ArtmindViewer.open({label, buttons: [[label, title, onClick], ...], keys: {"+": fn},
//                       onResize, onClose, className}) -> {overlay, bar, close}
//   ArtmindViewer.button(label, title, onClick) -> <button class="viewer-btn">
(function () {
  function button(label, title, onClick) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "viewer-btn";
    b.textContent = label;
    b.title = title;
    b.addEventListener("click", onClick);
    return b;
  }

  function open({ label = "Viewer", buttons = [], keys = {}, onResize = null, onClose = null, className = "" } = {}) {
    const opener = document.activeElement;
    const overlay = document.createElement("div");
    overlay.className = className ? `viewer-overlay ${className}` : "viewer-overlay";
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-modal", "true");
    overlay.setAttribute("aria-label", label);
    const bar = document.createElement("div");
    bar.className = "viewer-bar";

    function onKey(e) {
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); }
      else if (Object.prototype.hasOwnProperty.call(keys, e.key)) keys[e.key]();
    }
    let closed = false;
    function close() {
      if (closed) return;
      closed = true;
      document.removeEventListener("keydown", onKey, true);
      if (onResize) window.removeEventListener("resize", onResize);
      overlay.remove();
      if (onClose) onClose();
      if (opener && opener.focus) opener.focus();
    }

    for (const [text, title, fn] of buttons) bar.appendChild(button(text, title, fn));
    const closeBtn = button("close", "Close (Esc)", close);
    bar.appendChild(closeBtn);
    overlay.appendChild(bar);
    document.addEventListener("keydown", onKey, true);
    if (onResize) window.addEventListener("resize", onResize);
    document.body.appendChild(overlay);
    closeBtn.focus();
    return { overlay, bar, close };
  }

  window.ArtmindViewer = { button, open };
})();
