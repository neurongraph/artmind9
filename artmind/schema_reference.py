"""Parse `domains/schemas/*_schema.yaml` extraction prompts into structured data
and render them as an HTML fragment for the admin-ui's "Schemas" tab.

Used by `GET /api/schema-reference` (see `webui/dashboard_routes.py`) to turn a
domain family's schema files (e.g. all `banking_*_schema.yaml`) into one
browsable view — entity classes, property guidance, and the relationship
model — without having to read the raw LLM prompt text. Regenerated from the
run folder's live schemas on every request; there is no checked-in copy.
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


def list_schema_families(schemas_dir: Path) -> list[str]:
    """Domain families derivable from `*_schema.yaml` filenames in `schemas_dir`.

    A "family" is the part of a schema's stem before the first `.` -- matching
    exactly what `PREFIX` in `find_family_schemas` globs against, so a name
    returned here is always guaranteed to resolve. `banking.cases_schema.yaml`
    and `banking_schema.yaml` both belong to family "banking".
    """
    if not schemas_dir.exists():
        return []
    families = set()
    for f in schemas_dir.glob("*_schema.yaml"):
        stem = f.name[: -len("_schema.yaml")]
        families.add(stem.split(".")[0])
    return sorted(families)


def find_family_schemas(schemas_dir: Path, prefix: str) -> list[Path]:
    return sorted(schemas_dir.glob(f"{prefix}*_schema.yaml"))


# ── HTML rendering ───────────────────────────────────────────────────────────
#
# Output is a **fragment**: bare `sr-`-prefixed markup, no <style>/<script>/
# <title>/:root of its own. Styled by dashboard.css, themed by the admin-ui's
# CSS variables, filtered by the search box wired up in dashboard.js -- same
# division of labour as artmind/cli_guide.py for the CLI guide tab.


def _esc(s: str | None) -> str:
    return html.escape(s or "", quote=True)


def _slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _chip(text: str, css_class: str = "sr-chip") -> str:
    return f'<span class="{css_class}">{_esc(text)}</span>'


def _short_title(name: str, prefix: str) -> str:
    """Derive a nav-friendly label, e.g. 'banking_risk_governance' + 'banking' -> 'Risk Governance'.

    Also handles dotted hierarchical names, e.g. 'banking.policy' + 'banking' -> 'Policy'.
    """
    label = name
    if prefix and label.startswith(prefix):
        label = label[len(prefix) :].lstrip("_.")
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
        + (f' <span class="sr-hint">— {_esc(p["hint"])}</span>' if p["hint"] else "")
        + "</li>"
        for p in e["properties"]
    )
    props_block = ""
    if props_html:
        props_block = (
            f'<details class="sr-props"><summary>Properties ({len(e["properties"])})</summary>'
            f"<ul>{props_html}</ul></details>"
        )
    return (
        f'<article class="sr-card" id="{schema_id}-{class_slug}" data-search="{_esc(search_text)}">'
        f'<header><h4>{_esc(e["class"])}</h4></header>'
        f'<p class="sr-desc">{_esc(e["description"])}</p>'
        f'<div class="sr-types">{types_html}</div>'
        f"{props_block}"
        f"</article>"
    )


def _render_relationship_row(schema_id: str, r: dict[str, Any]) -> str:
    search_text = " ".join([r["a"], r["b"]] + r["types"]).lower()
    types_html = "".join(_chip(t, "sr-chip sr-chip-rel") for t in r["types"])
    return (
        f'<tr data-search="{_esc(search_text)}">'
        f'<td class="sr-rel-entity"><a href="#{schema_id}-{_slugify(r["a"])}">{_esc(r["a"])}</a></td>'
        f'<td class="sr-rel-arrow">&harr;</td>'
        f'<td class="sr-rel-entity"><a href="#{schema_id}-{_slugify(r["b"])}">{_esc(r["b"])}</a></td>'
        f'<td class="sr-rel-types">{types_html}</td>'
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
    return f'<div class="sr-temporal">{"".join(parts)}</div>' if parts else ""


def render_fragment(schemas: list[dict[str, Any]], prefix: str = "") -> str:
    """Render the parsed schema list as a bare HTML fragment."""
    total_entities = sum(len(s["entities"]) for s in schemas)
    total_rels = sum(len(s["relationships"]) for s in schemas)

    overview = f'''
  <div class="sr-overview">
    <div class="sr-stat"><div class="sr-stat-num">{len(schemas)}</div><div class="sr-stat-label">Schemas</div></div>
    <div class="sr-stat"><div class="sr-stat-num">{total_entities}</div><div class="sr-stat-label">Entity classes</div></div>
    <div class="sr-stat"><div class="sr-stat-num">{total_rels}</div><div class="sr-stat-label">Relationship pairs</div></div>
  </div>
'''

    sections = []
    for s in schemas:
        schema_id = _slugify(s["name"])
        short_title = _short_title(s["name"], prefix)
        entity_cards = "\n".join(_render_entity_card(schema_id, e) for e in s["entities"])
        rel_rows = "\n".join(_render_relationship_row(schema_id, r) for r in s["relationships"])
        sections.append(f"""
    <section class="sr-schema" id="{schema_id}">
      <button type="button" class="sr-schema-head">
        <span class="sr-schema-toggle" aria-hidden="true">&#9660;</span>
        <h3>{_esc(short_title)}</h3>
        <code class="sr-file-badge">{_esc(s["file"])}</code>
        <span class="sr-schema-counts">{len(s["entities"])} entities &middot; {len(s["relationships"])} relationships</span>
      </button>
      <div class="sr-schema-body">
        <p class="sr-schema-desc">{_esc(s["description"])}</p>
        {_render_temporal(s.get("temporal"))}
        <div class="sr-subhead">Entity classes <span class="sr-count-badge">{len(s["entities"])}</span></div>
        <div class="sr-grid">
          {entity_cards}
        </div>
        <div class="sr-subhead">Relationship model <span class="sr-count-badge">{len(s["relationships"])}</span></div>
        <div class="table-scroll">
          <table class="sr-table">
            <thead><tr><th>Entity</th><th></th><th>Entity</th><th>Relationship types</th></tr></thead>
            <tbody>
              {rel_rows}
            </tbody>
          </table>
        </div>
      </div>
    </section>
    """)

    return overview + "".join(sections)
