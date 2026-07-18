"use strict";

// Admin-only help layer: suggested-action chips + concept drawer, both driven
// by /api/help/concepts (generated from SKILL.md front-matter + CLI docstrings —
// see artmind/webui/help.py). Never hand-type concept text here.

function helpEl(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

function fillPrompt(text) {
  const promptEl = document.getElementById("prompt");
  promptEl.value = text;
  promptEl.dispatchEvent(new Event("input", { bubbles: true }));
  promptEl.focus();
}

async function loadConcepts() {
  const response = await fetch("/api/help/concepts");
  if (!response.ok) return [];
  return response.json();
}

function renderChips(concepts) {
  const container = document.getElementById("suggested-chips");
  const skillConcepts = concepts.filter((c) => c.source === "skill");
  for (const concept of skillConcepts) {
    const chip = helpEl("button", "chip", concept.title);
    chip.type = "button";
    if (concept.destructive) chip.appendChild(helpEl("span", "chip-warn", "!"));
    chip.addEventListener("click", () => {
      fillPrompt(`Help me with ${concept.title}: ${concept.description}`);
    });
    container.appendChild(chip);
  }
}

function renderConceptDrawer(concepts) {
  const list = document.getElementById("help-concepts-list");
  list.innerHTML = "";
  for (const concept of concepts) {
    const card = helpEl("div", "tool-card");
    const head = helpEl("div", "tool-head", concept.title);
    if (concept.destructive) head.appendChild(helpEl("span", "destructive-badge", "destructive"));
    card.appendChild(head);
    card.appendChild(helpEl("div", "dash-note", concept.description));
    list.appendChild(card);
  }
}

// ── help drawer open/close ────────────────────────────────────────────
const helpDrawerEl = document.getElementById("help-drawer");
const helpDrawerCloseEl = document.getElementById("help-drawer-close");
const helpToggleEl = document.getElementById("help-toggle");
helpDrawerEl.inert = true;

function openHelpDrawer() {
  helpDrawerEl.classList.add("open");
  helpDrawerEl.setAttribute("aria-hidden", "false");
  helpDrawerEl.inert = false;
  helpDrawerCloseEl.removeAttribute("tabindex");
}

function closeHelpDrawer() {
  helpDrawerEl.classList.remove("open");
  helpDrawerEl.setAttribute("aria-hidden", "true");
  helpDrawerEl.inert = true;
  helpDrawerCloseEl.setAttribute("tabindex", "-1");
}

helpToggleEl.addEventListener("click", () => {
  if (helpDrawerEl.classList.contains("open")) closeHelpDrawer();
  else openHelpDrawer();
});
helpDrawerCloseEl.addEventListener("click", closeHelpDrawer);

loadConcepts().then((concepts) => {
  renderChips(concepts);
  renderConceptDrawer(concepts);
});
