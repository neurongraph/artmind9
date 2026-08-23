"""Read `domains/schemas/*_schema.yaml`'s structured `entity_types` map and
render it as an HTML fragment for the admin-ui's "Schemas" tab.

Used by `GET /api/schema-reference` (see `webui/dashboard_routes.py`) to turn a
domain family's schema files (e.g. all `banking_*_schema.yaml`) into one
browsable view -- entity classes, property guidance, and the relationship
model -- without having to read the raw LLM prompt text. Regenerated from the
run folder's live schemas on every request; there is no checked-in copy.

Pre-redesign this regex-parsed the hand-written prose prompts back apart.
Now `entity_types` already IS that structured data, so there is nothing left
to parse -- this module just reshapes it for rendering, and additionally
renders the actual ASSEMBLED prompt (via `artmind.prompt_builder`) so an
operator can see exactly what the LLM receives.
"""

from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any

import yaml

from artmind.prompt_builder import (
    assemble_entities_prompt,
    assemble_properties_prompt,
    assemble_relationships_prompt,
    relationship_pairs,
)


def build_schema_dict(schema_path: Path) -> dict[str, Any]:
    """Load one `*_schema.yaml` and return its parsed, render-ready form."""
    data = yaml.safe_load(schema_path.read_text()) or {}
    entity_types = data.get("entity_types") or {}

    entities = []
    for cls, decl in entity_types.items():
        properties = [
            {"name": name, "hint": (prop_decl or {}).get("hint", "") if isinstance(prop_decl, dict) else ""}
            for name, prop_decl in (decl.get("properties") or {}).items()
        ]
        entities.append(
            {
                "class": cls,
                "kind": decl.get("kind", ""),
                "description": decl.get("description", ""),
                "types": decl.get("type_examples", []),
                "properties": properties,
            }
        )

    relationships = [
        {"a": a, "b": b, "types": types} for a, b, types in relationship_pairs(entity_types)
    ]

    assembled_prompts = {}
    if entity_types:
        assembled_prompts = {
            "entities": assemble_entities_prompt(data),
            "properties": assemble_properties_prompt(data),
            "relationships": assemble_relationships_prompt(data),
        }

    return {
        "file": schema_path.name,
        "name": data.get("name", schema_path.stem),
        "description": data.get("description", ""),
        "temporal": data.get("temporal"),
        "entities": entities,
        "relationships": relationships,
        "assembled_prompts": assembled_prompts,
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
        [e["class"], e["description"], e.get("kind", ""), " ".join(e["types"])]
        + [p["name"] for p in e["properties"]]
    ).lower()
    kind_chip = _chip(e["kind"], f'sr-chip sr-chip-kind sr-chip-kind-{e["kind"]}') if e.get("kind") else ""
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
        f'<header><h4>{_esc(e["class"])}</h4>{kind_chip}</header>'
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
    if t.get("relative_anchor"):
        parts.append(f'<div><strong>Relative anchor:</strong> <code>{_esc(t["relative_anchor"])}</code></div>')
    return f'<div class="sr-temporal">{"".join(parts)}</div>' if parts else ""


def _render_assembled_prompts(assembled: dict[str, str]) -> str:
    if not assembled:
        return ""
    sections = "".join(
        f"<details class='sr-raw-prompt'><summary>{_esc(kind.title())} prompt</summary>"
        f"<pre>{_esc(text)}</pre></details>"
        for kind, text in assembled.items()
    )
    return f'<div class="sr-raw-prompts"><div class="sr-subhead">Assembled prompts (as sent to the LLM)</div>{sections}</div>'


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
        {_render_assembled_prompts(s.get("assembled_prompts") or {})}
      </div>
    </section>
    """)

    return overview + "".join(sections)
