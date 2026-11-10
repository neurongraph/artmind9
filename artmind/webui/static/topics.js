"use strict";

// Topics tab: topic plans with their freshness, and topic pages, read from disk on every
// load. Uses el() and api() from dashboard.js; page markdown is sanitized with DOMPurify.

const topicsPlansEl = document.getElementById("topics-plans");
const topicsPagesEl = document.getElementById("topics-pages");
const topicsDetailEl = document.getElementById("topics-detail");

function freshnessLine(f) {
  if (!f) return "not checked yet";
  let text = `${f.status}` + (f.checked_at ? ` (checked ${f.checked_at})` : "");
  if ((f.reasons || []).length) text += ` — ${f.reasons.join("; ")}`;
  return text;
}

function planCard(plan) {
  const card = el("div", "ins-card");
  card.appendChild(el("h3", "", plan.topic || plan.slug));
  card.appendChild(el("p", "dash-note", `${plan.slug} · v${plan.version} · ${plan.domains.join(", ")} · refresh: ${plan.refresh}`));
  const subjects = el("ol");
  for (const s of plan.subjects || []) subjects.appendChild(el("li", "", s.text));
  card.appendChild(subjects);
  const fresh = el("p", "dash-note", `Freshness: ${freshnessLine(plan.freshness)}`);
  card.appendChild(fresh);
  const auto = plan.freshness && plan.freshness.auto_refresh;
  if (auto) card.appendChild(el("p", "dash-note", `Automatic refresh: ${auto.status} — ${auto.reason || ""}`));
  const btn = el("button", "btn-secondary", "Check now");
  btn.type = "button";
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    try {
      const res = await api(`/api/topics/${encodeURIComponent(plan.slug)}/check`, { method: "POST" });
      fresh.textContent = `Freshness: ${freshnessLine(res)}`;
    } catch (err) {
      fresh.textContent = `Check failed: ${err.message}`;
    } finally {
      btn.disabled = false;
    }
  });
  card.appendChild(btn);
  return card;
}

async function showPage(slug) {
  topicsDetailEl.innerHTML = "";
  topicsDetailEl.appendChild(el("p", "dash-empty", "Loading…"));
  try {
    const page = await api(`/api/topics/${encodeURIComponent(slug)}/page`);
    topicsDetailEl.innerHTML = "";
    topicsDetailEl.appendChild(el("h3", "", page.topic || slug));
    const body = el("div");
    body.innerHTML = DOMPurify.sanitize(marked.parse(page.body || ""));
    topicsDetailEl.appendChild(body);
  } catch (err) {
    topicsDetailEl.innerHTML = "";
    topicsDetailEl.appendChild(el("p", "dash-note", `Failed: ${err.message}`));
  }
}

async function loadTopics() {
  topicsPlansEl.innerHTML = "";
  topicsPagesEl.innerHTML = "";
  const data = await api("/api/topics");
  if (!(data.plans || []).length) topicsPlansEl.appendChild(el("p", "dash-empty", "No topic plans yet."));
  for (const plan of data.plans || []) topicsPlansEl.appendChild(planCard(plan));
  for (const bad of data.invalid || []) {
    topicsPlansEl.appendChild(el("p", "dash-note", `${bad.slug}: invalid — ${bad.error}`));
  }
  if (!(data.pages || []).length) topicsPagesEl.appendChild(el("p", "dash-empty", "No topic pages."));
  for (const page of data.pages || []) {
    const btn = el("button", "btn-secondary", `${page.topic || page.slug} (${page.sources} sources)`);
    btn.type = "button";
    btn.addEventListener("click", () => showPage(page.slug));
    topicsPagesEl.appendChild(btn);
  }
}

document.querySelector('.tab[data-tab="topics"]').addEventListener("click", () => {
  loadTopics().catch((err) => {
    topicsPlansEl.innerHTML = "";
    topicsPlansEl.appendChild(el("p", "dash-note", `Failed: ${err.message}`));
  });
});
