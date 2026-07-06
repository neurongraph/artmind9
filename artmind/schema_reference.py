"""Parse `domains/schemas/*_schema.yaml` extraction prompts into structured data
and render them as one browsable, self-contained HTML reference page.

Used by `artmind domains render-html <prefix>` to turn a family of schema
files (e.g. all `banking_*_schema.yaml`) into a single consumable document —
entity classes, property guidance, and the relationship model — without
having to read the raw LLM prompt text.
"""

from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any

import yaml

# ── prompt parsing ───────────────────────────────────────────────────────────
#
# Every schema's entities_prompt / properties_prompt / relationships_prompt
# follows the same hand-written structure (see any domains/schemas/*.yaml):
# a banner-delimited section listing "CLASS_NAME\n  description\n  example
# type values: a | b | c", a "For CLASS_NAME, consider:\n  - prop (hint)"
# properties section, and a "A ↔ B:\n  type1, type2" relationships section.


def parse_entities(entities_prompt: str) -> list[dict[str, Any]]:
    """Extract entity_class / description / example type values."""
    m = re.search(
        r"ENTITY TYPES YOU MUST EXTRACT:\s*\n━+\s*\n(.*?)\n━+\s*\nEXTRACTION RULES:",
        entities_prompt,
        re.S,
    )
    if not m:
        return []
    body = re.sub(r"^Use ONLY these entity_classes.*?\n\n", "", m.group(1), flags=re.S)

    entities = []
    for block in re.split(r"\n\n+", body.strip()):
        lines = block.strip("\n").split("\n")
        cls = lines[0].strip()
        if not re.match(r"^[A-Z][A-Z0-9_]*$", cls):
            continue
        desc_lines, types_line = [], ""
        for line in lines[1:]:
            line = line.strip()
            if line.startswith("example type values:"):
                types_line = line[len("example type values:") :].strip()
            else:
                desc_lines.append(line)
        entities.append(
            {
                "class": cls,
                "description": " ".join(desc_lines).strip(),
                "types": [t.strip() for t in types_line.split("|") if t.strip()],
            }
        )
    return entities


def parse_properties(properties_prompt: str) -> dict[str, list[dict[str, str]]]:
    """Extract, per entity class, the `- name (hint)` property bullets."""
    props: dict[str, list[dict[str, str]]] = {}
    pattern = re.compile(
        r"For ([A-Z][A-Z0-9_]*), consider:\n(.*?)"
        r"(?=\n\nFor [A-Z][A-Z0-9_]*, consider:|\n\n━+|\Z)",
        re.S,
    )
    for m in pattern.finditer(properties_prompt):
        cls, body = m.group(1), m.group(2)
        items = []
        for line in body.split("\n"):
            line = line.strip()
            if not line.startswith("- "):
                continue
            line = line[2:]
            pm = re.match(r"^([a-zA-Z0-9_]+)\s*(?:\((.*)\))?$", line)
            if pm:
                items.append({"name": pm.group(1), "hint": pm.group(2) or ""})
            else:
                items.append({"name": line, "hint": ""})
        if items:
            props[cls] = items
    return props


def parse_relationships(relationships_prompt: str) -> list[dict[str, Any]]:
    """Extract the `A ↔ B: type1, type2, ...` relationship-model pairs."""
    m = re.search(
        r"COMMON rel_type VALUES:\s*\n━+\s*\n(.*?)\n━+\s*\nEXTRACTION RULES:",
        relationships_prompt,
        re.S,
    )
    if not m:
        return []

    rels = []
    for block in re.split(r"\n\n+", m.group(1).strip()):
        lines = [l for l in block.strip("\n").split("\n") if l.strip()]
        if not lines:
            continue
        header = re.match(r"^(.+?)\s*↔\s*(.+?):$", lines[0].strip())
        if not header:
            continue
        rest = " ".join(l.strip() for l in lines[1:])
        rels.append(
            {
                "a": header.group(1).strip(),
                "b": header.group(2).strip(),
                "types": [t.strip() for t in rest.split(",") if t.strip()],
            }
        )
    return rels


def build_schema_dict(schema_path: Path) -> dict[str, Any]:
    """Load one `*_schema.yaml` and return its parsed, render-ready form."""
    data = yaml.safe_load(schema_path.read_text())
    entities = parse_entities(data.get("entities_prompt", ""))
    props_by_class = parse_properties(data.get("properties_prompt", ""))
    for e in entities:
        e["properties"] = props_by_class.get(e["class"], [])
    return {
        "file": schema_path.name,
        "name": data.get("name", schema_path.stem),
        "description": data.get("description", ""),
        "temporal": data.get("temporal"),
        "entities": entities,
        "relationships": parse_relationships(data.get("relationships_prompt", "")),
    }


# ── HTML rendering ───────────────────────────────────────────────────────────


def _esc(s: str | None) -> str:
    return html.escape(s or "", quote=True)


def _slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _chip(text: str, css_class: str = "chip") -> str:
    return f'<span class="{css_class}">{_esc(text)}</span>'


def _short_title(name: str, prefix: str) -> str:
    """Derive a nav-friendly label, e.g. 'banking_risk_governance' + 'banking' -> 'Risk Governance'."""
    label = name
    if prefix and label.startswith(prefix):
        label = label[len(prefix) :].lstrip("_")
    return (label or name).replace("_", " ").title()


def _render_entity_card(schema_id: str, e: dict[str, Any]) -> str:
    class_slug = _slugify(e["class"])
    search_text = " ".join(
        [e["class"], e["description"], " ".join(e["types"])]
        + [p["name"] for p in e["properties"]]
    ).lower()
    types_html = "".join(_chip(t) for t in e["types"])
    props_html = "".join(
        f'<li><code>{_esc(p["name"])}</code>'
        + (f' <span class="hint">— {_esc(p["hint"])}</span>' if p["hint"] else "")
        + "</li>"
        for p in e["properties"]
    )
    props_block = ""
    if props_html:
        props_block = (
            f'<details class="props"><summary>Properties ({len(e["properties"])})</summary>'
            f"<ul>{props_html}</ul></details>"
        )
    return (
        f'<article class="entity-card" id="{schema_id}-{class_slug}" data-search="{_esc(search_text)}">'
        f'<header><h4>{_esc(e["class"])}</h4></header>'
        f'<p class="desc">{_esc(e["description"])}</p>'
        f'<div class="types">{types_html}</div>'
        f"{props_block}"
        f"</article>"
    )


def _render_relationship_row(schema_id: str, r: dict[str, Any]) -> str:
    search_text = " ".join([r["a"], r["b"]] + r["types"]).lower()
    types_html = "".join(_chip(t, "chip rel-chip") for t in r["types"])
    return (
        f'<tr data-search="{_esc(search_text)}">'
        f'<td class="rel-entity"><a href="#{schema_id}-{_slugify(r["a"])}">{_esc(r["a"])}</a></td>'
        f'<td class="rel-arrow">&harr;</td>'
        f'<td class="rel-entity"><a href="#{schema_id}-{_slugify(r["b"])}">{_esc(r["b"])}</a></td>'
        f'<td class="rel-types">{types_html}</td>'
        f"</tr>"
    )


def _render_temporal(t: dict[str, Any] | None) -> str:
    if not t:
        return ""
    parts = []
    doc = t.get("document") or {}
    if doc:
        fields = []
        for k, v in doc.items():
            vv = v if isinstance(v, str) else ", ".join(v)
            fields.append(f"<code>{_esc(k)}</code>: {_esc(vv)}")
        parts.append(f'<div><strong>Document:</strong> {" &middot; ".join(fields)}</div>')
    ent = t.get("entities") or {}
    if ent:
        rows = []
        for k, v in ent.items():
            vv = ", ".join(f"{kk}={vvv}" for kk, vvv in v.items())
            rows.append(f"<code>{_esc(k)}</code> ({_esc(vv)})")
        parts.append(f'<div><strong>Temporal entities:</strong> {" &middot; ".join(rows)}</div>')
    if t.get("relative_anchor"):
        parts.append(f'<div><strong>Relative anchor:</strong> <code>{_esc(t["relative_anchor"])}</code></div>')
    return f'<div class="temporal-box">{"".join(parts)}</div>' if parts else ""


_CSS = """
:root {
  --bg: #f7f8fa; --bg-elevated: #ffffff; --text: #1a1d23; --text-muted: #5b6270;
  --border: #e2e5ea; --accent: #2f6fed; --accent-soft: #e8f0fe; --chip-bg: #eef1f6;
  --chip-text: #3a4150; --rel-chip-bg: #fff4e5; --rel-chip-text: #8a5a00;
  --code-bg: #f0f2f5; --shadow: 0 1px 2px rgba(16,24,40,0.06); --sidebar-w: 260px;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #14161a; --bg-elevated: #1c1f26; --text: #e7e9ee; --text-muted: #9aa1ae;
    --border: #2a2e37; --accent: #6d9bff; --accent-soft: #1e2a44; --chip-bg: #262a33;
    --chip-text: #c3c9d4; --rel-chip-bg: #3a2f14; --rel-chip-text: #e0b45c;
    --code-bg: #20232b; --shadow: 0 1px 2px rgba(0,0,0,0.4);
  }
}
* { box-sizing: border-box; }
body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; background: var(--bg); color: var(--text); line-height: 1.5; }
code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 0.85em; background: var(--code-bg); padding: 0.1em 0.4em; border-radius: 4px; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
header.top { position: sticky; top: 0; z-index: 20; background: var(--bg-elevated); border-bottom: 1px solid var(--border); padding: 14px 24px; display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
header.top h1 { font-size: 1.05rem; margin: 0; white-space: nowrap; }
header.top .subtitle { font-size: 0.8rem; color: var(--text-muted); margin-right: auto; }
#search { flex: 1 1 240px; max-width: 360px; padding: 8px 12px; border-radius: 8px; border: 1px solid var(--border); background: var(--bg); color: var(--text); font-size: 0.9rem; }
#search:focus { outline: 2px solid var(--accent); outline-offset: 1px; }
#match-count { font-size: 0.78rem; color: var(--text-muted); white-space: nowrap; }
.layout { display: flex; max-width: 1400px; margin: 0 auto; }
nav.sidebar { width: var(--sidebar-w); flex-shrink: 0; position: sticky; top: 57px; align-self: flex-start; height: calc(100vh - 57px); overflow-y: auto; padding: 16px 8px; border-right: 1px solid var(--border); }
.nav-link { display: flex; justify-content: space-between; align-items: center; padding: 8px 12px; border-radius: 8px; font-size: 0.88rem; color: var(--text); margin-bottom: 2px; }
.nav-link:hover { background: var(--accent-soft); text-decoration: none; }
.nav-count { font-size: 0.72rem; color: var(--text-muted); background: var(--chip-bg); padding: 1px 7px; border-radius: 10px; }
main { flex: 1; min-width: 0; padding: 24px 32px 80px; }
.overview { background: var(--bg-elevated); border: 1px solid var(--border); border-radius: 12px; padding: 20px 24px; margin-bottom: 32px; box-shadow: var(--shadow); }
.overview h2 { margin-top: 0; }
.stat-row { display: flex; gap: 28px; flex-wrap: wrap; margin-top: 12px; }
.stat .num { font-size: 1.6rem; font-weight: 600; }
.stat .label { font-size: 0.78rem; color: var(--text-muted); }
.schema-section { margin-bottom: 48px; padding-top: 8px; scroll-margin-top: 70px; }
.schema-header { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; }
.schema-header h2 { margin: 0 0 4px; }
.file-badge { color: var(--text-muted); }
.schema-desc { color: var(--text-muted); max-width: 900px; margin-top: 4px; }
.temporal-box { background: var(--accent-soft); border: 1px solid var(--border); border-radius: 8px; padding: 10px 14px; font-size: 0.82rem; margin: 12px 0 20px; display: flex; flex-direction: column; gap: 4px; }
.subhead { font-size: 0.95rem; text-transform: uppercase; letter-spacing: 0.04em; color: var(--text-muted); margin: 28px 0 12px; display: flex; align-items: center; gap: 8px; }
.count-badge { background: var(--chip-bg); color: var(--chip-text); font-size: 0.7rem; padding: 1px 8px; border-radius: 10px; text-transform: none; letter-spacing: normal; }
.entity-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 14px; }
.entity-card { background: var(--bg-elevated); border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; box-shadow: var(--shadow); scroll-margin-top: 70px; }
.entity-card h4 { margin: 0 0 6px; font-size: 0.92rem; letter-spacing: 0.02em; }
.entity-card .desc { font-size: 0.83rem; color: var(--text-muted); margin: 0 0 10px; }
.types { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 8px; }
.chip { background: var(--chip-bg); color: var(--chip-text); font-size: 0.72rem; padding: 2px 9px; border-radius: 12px; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.rel-chip { background: var(--rel-chip-bg); color: var(--rel-chip-text); }
details.props { margin-top: 6px; font-size: 0.8rem; }
details.props summary { cursor: pointer; color: var(--accent); font-size: 0.78rem; }
details.props ul { margin: 8px 0 0; padding-left: 18px; }
details.props li { margin-bottom: 4px; }
.hint { color: var(--text-muted); }
.table-wrap { overflow-x: auto; border: 1px solid var(--border); border-radius: 10px; }
table.rel-table { width: 100%; border-collapse: collapse; font-size: 0.83rem; min-width: 560px; }
table.rel-table th { text-align: left; padding: 10px 14px; background: var(--chip-bg); border-bottom: 1px solid var(--border); font-weight: 600; font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.03em; color: var(--text-muted); }
table.rel-table td { padding: 9px 14px; border-bottom: 1px solid var(--border); vertical-align: top; }
table.rel-table tr:last-child td { border-bottom: none; }
.rel-entity code, .rel-entity a { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 0.82rem; }
.rel-arrow { color: var(--text-muted); text-align: center; width: 24px; }
.rel-types { display: flex; flex-wrap: wrap; gap: 5px; }
.hidden { display: none !important; }
@media (max-width: 860px) {
  .layout { flex-direction: column; }
  nav.sidebar { position: static; width: 100%; height: auto; display: flex; overflow-x: auto; border-right: none; border-bottom: 1px solid var(--border); padding: 8px 12px; gap: 4px; }
  .nav-link { flex-shrink: 0; }
  main { padding: 20px 16px 60px; }
}
"""

_JS = """
const searchInput = document.getElementById('search');
const matchCount = document.getElementById('match-count');
const cards = Array.from(document.querySelectorAll('.entity-card'));
const rows = Array.from(document.querySelectorAll('table.rel-table tbody tr'));
function applyFilter() {
  const q = searchInput.value.trim().toLowerCase();
  let shown = 0;
  cards.forEach(c => {
    const match = !q || c.dataset.search.includes(q);
    c.classList.toggle('hidden', !match);
    if (match) shown++;
  });
  rows.forEach(r => {
    const match = !q || r.dataset.search.includes(q);
    r.classList.toggle('hidden', !match);
  });
  matchCount.textContent = q ? `${shown} / ${cards.length} entities` : '';
}
searchInput.addEventListener('input', applyFilter);
"""


def render_html(schemas: list[dict[str, Any]], title: str, prefix: str = "", subtitle: str = "") -> str:
    """Render the parsed schema list into one self-contained HTML page."""
    nav_items, sections = [], []
    total_entities = sum(len(s["entities"]) for s in schemas)
    total_rels = sum(len(s["relationships"]) for s in schemas)

    for s in schemas:
        schema_id = _slugify(s["name"])
        short_title = _short_title(s["name"], prefix)
        nav_items.append(
            f'<a href="#{schema_id}" class="nav-link">'
            f'<span class="nav-title">{_esc(short_title)}</span>'
            f'<span class="nav-count">{len(s["entities"])}</span></a>'
        )
        entity_cards = "\n".join(_render_entity_card(schema_id, e) for e in s["entities"])
        rel_rows = "\n".join(_render_relationship_row(schema_id, r) for r in s["relationships"])
        sections.append(f"""
    <section class="schema-section" id="{schema_id}">
      <div class="schema-header">
        <h2>{_esc(short_title)}</h2>
        <code class="file-badge">{_esc(s["file"])}</code>
      </div>
      <p class="schema-desc">{_esc(s["description"])}</p>
      {_render_temporal(s.get("temporal"))}
      <h3 class="subhead">Entity Classes <span class="count-badge">{len(s["entities"])}</span></h3>
      <div class="entity-grid">
        {entity_cards}
      </div>
      <h3 class="subhead">Relationship Model <span class="count-badge">{len(s["relationships"])}</span></h3>
      <div class="table-wrap">
        <table class="rel-table">
          <thead><tr><th>Entity</th><th></th><th>Entity</th><th>Relationship Types</th></tr></thead>
          <tbody>
            {rel_rows}
          </tbody>
        </table>
      </div>
    </section>
    """)

    return f"""<title>{_esc(title)}</title>
<style>{_CSS}</style>
<header class="top">
  <h1>{_esc(title)}</h1>
  <span class="subtitle">{_esc(subtitle)}</span>
  <input id="search" type="text" placeholder="Search entity classes, types, properties, relationships…" autocomplete="off" />
  <span id="match-count"></span>
</header>
<div class="layout">
  <nav class="sidebar">
    <a href="#overview" class="nav-link"><span class="nav-title">Overview</span></a>
    {"".join(nav_items)}
  </nav>
  <main>
    <section class="overview" id="overview">
      <h2>Overview</h2>
      <p>This page consolidates {len(schemas)} schema file(s) matching <code>{_esc(prefix)}*_schema.yaml</code> in <code>domains/schemas/</code> into one browsable reference. Each schema defines the entity classes, property guidance, and relationship model used to extract a knowledge graph from a given document type. Regenerate with <code>artmind domains render-html {_esc(prefix)}</code>.</p>
      <div class="stat-row">
        <div class="stat"><div class="num">{len(schemas)}</div><div class="label">Schemas</div></div>
        <div class="stat"><div class="num">{total_entities}</div><div class="label">Entity classes</div></div>
        <div class="stat"><div class="num">{total_rels}</div><div class="label">Relationship pairs</div></div>
      </div>
    </section>
    {"".join(sections)}
  </main>
</div>
<script>{_JS}</script>
"""
