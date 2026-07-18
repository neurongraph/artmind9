#!/usr/bin/env python3
"""Generate a self-contained interactive HTML guide from the artmind Click CLI.

Run from repo root:
    uv run python scripts/generate_cli_guide.py

Check mode (exit non-zero if guide would change):
    uv run python scripts/generate_cli_guide.py --check
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

# Import artmind's CLI. In artmind/cli.py Click is aliased as `click`.
import artmind.cli as cli_module

click = cli_module.click
cli = cli_module.cli

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = PROJECT_ROOT / "docs" / "artmind-cli-guide.html"
EXAMPLES_FILE = PROJECT_ROOT / "scripts" / "cli_guide_examples.json"


# ---------------------------------------------------------------------------
# Category styling and ordering taken from click.rich_click.COMMAND_GROUPS.
# ---------------------------------------------------------------------------

CATEGORY_META: dict[str, tuple[str, str, str]] = {
    "Setup & tools": ("⚙", "green", "Initialize tables and constraints"),
    "Domains": ("◆", "accent", "Manage domain schemas and hierarchy"),
    "Ingestion": ("↓", "blue", "Document ingestion, graph building, and refinement"),
    "Query": ("?", "cyan", "Knowledge graph and vector index queries"),
    "Documents": ("□", "orange", "Manage ingested documents"),
    "Updates": ("✎", "pink", "Add and update facts from natural language"),
    "Sessions": ("◉", "yellow", "Save and restore the Neo4j graph between sessions"),
}

COLOR_MAP: dict[str, tuple[str, str]] = {
    "accent": ("rgba(124,106,239,0.08)", "#9d8ff5"),
    "green": ("rgba(74,222,128,0.08)", "#4ade80"),
    "blue": ("rgba(96,165,250,0.08)", "#60a5fa"),
    "cyan": ("rgba(34,211,238,0.08)", "#22d3ee"),
    "orange": ("rgba(251,146,60,0.08)", "#fb923c"),
    "pink": ("rgba(244,114,182,0.08)", "#f472b6"),
    "yellow": ("rgba(250,204,21,0.08)", "#facc15"),
    "red": ("rgba(248,113,113,0.08)", "#f87171"),
}


def load_examples() -> dict[str, dict]:
    if EXAMPLES_FILE.exists():
        return json.loads(EXAMPLES_FILE.read_text())
    return {}


# ---------------------------------------------------------------------------
# CLI introspection helpers
# ---------------------------------------------------------------------------


def type_name(param) -> str:
    """Return a human-friendly type label for a Click parameter."""
    if getattr(param, "is_flag", False):
        return "FLAG"
    param_type = getattr(param, "type", None)
    if param_type is None:
        return "TEXT"
    class_name = type(param_type).__name__
    if class_name == "Path":
        return "PATH"
    if class_name == "Choice":
        return "ENUM"
    mapping = {
        "IntParamType": "INTEGER",
        "FloatParamType": "FLOAT",
        "StringParamType": "TEXT",
        "BoolParamType": "BOOL",
    }
    return mapping.get(class_name, class_name).upper()


def format_default(param) -> str | None:
    default = getattr(param, "default", None)
    if default is None:
        return None
    if isinstance(default, (list, tuple)):
        return None
    text = str(default)
    # Hide Click's internal sentinel values (common for required args/options).
    if text.startswith("Sentinel") or text.startswith("<") or text.endswith(">"):
        return None
    if isinstance(param.type, click.Choice):
        return text
    return text


def format_flags(param) -> str:
    opts = getattr(param, "opts", [])
    secondary = getattr(param, "secondary_opts", [])
    return ", ".join(opts + secondary) or f"--{param.name}"


def collect_params(cmd) -> list[dict]:
    args: list[dict] = []
    opts: list[dict] = []
    for param in cmd.params:
        info = {
            "name": param.name,
            "flags": format_flags(param),
            "type": type_name(param),
            "required": bool(getattr(param, "required", False)),
            "default": format_default(param),
            "multiple": bool(getattr(param, "multiple", False)),
            "is_flag": bool(getattr(param, "is_flag", False)),
            "help": getattr(param, "help", "") or "",
        }
        if isinstance(param, click.Argument):
            args.append(info)
        else:
            opts.append(info)
    return args + opts


def format_usage(path: str, params: list[dict]) -> str:
    """Build a usage line like: artmind domains add DOMAIN_FILE [OPTIONS]"""
    parts = [html.escape(path)]
    for p in params:
        if isinstance(p, dict) and p.get("_kind") == "argument":
            # handled below; we derive kind in caller
            pass
    # We actually mark kind in collect_params... so patch there, but avoid refactor complexity:
    args = [p for p in params if p.get("_kind") == "argument"]
    opts = [p for p in params if p.get("_kind") != "argument"]
    for p in args:
        token = p["name"].upper()
        if p["multiple"]:
            token += "..."
        token = html.escape(token)
        if p["required"]:
            parts.append(f'<span class="req">{token}</span>')
        else:
            parts.append(f'<span class="opt">[{token}]</span>')
    if opts:
        parts.append('<span class="opt">[OPTIONS]</span>')
    return " ".join(parts)


def build_search_tokens(path: str, cmd, params: list[dict]) -> str:
    tokens = [path.replace("-", " ")]
    tokens.append((cmd.help or "") + " " + (cmd.short_help or ""))
    for p in params:
        tokens.append(p["name"])
        tokens.append(p["flags"])
        tokens.append(p["help"])
    return html.escape(" ".join(t.lower() for t in tokens))


# ---------------------------------------------------------------------------
# HTML pieces
# ---------------------------------------------------------------------------


def esc(text: str) -> str:
    text = str(text).replace("\\b", "").replace("\b", "")
    text = html.escape(text)
    text = text.replace("\n", "<br>")
    return text


def param_table(params: list[dict], path: str, examples_data: dict) -> str:
    if not params:
        return ""
    rows = []
    for p in params:
        name_cell = f'<span class="opt-name">{esc(p["flags"])}</span>'
        type_cell = f'<span class="opt-type">{esc(p["type"])}</span>'
        if p["_kind"] == "argument":
            name_cell = f'<span class="opt-name" style="color:#fbbf24">{esc(p["name"].upper())}</span>'
        tags = []
        if p["required"]:
            tags.append('<span class="opt-required">required</span>')
        if p["default"] is not None:
            tags.append(f'<span class="opt-default">default: {esc(p["default"])}</span>')
        if p["multiple"]:
            tags.append('<span class="opt-default">repeatable</span>')
        tag_cell = " ".join(tags)
        help_cell = esc(p["help"])
        rows.append(f"<tr><td>{name_cell}<br>{type_cell}</td><td>{help_cell}</td><td>{tag_cell}</td></tr>")

    return f'''
      <div class="cmd-options-title">Options &amp; Arguments</div>
      <table class="opt-table">
        {''.join(rows)}
      </table>
'''


def example_block(path: str, examples_data: dict) -> str:
    data = examples_data.get(path, {})
    snippets = data.get("examples", [])
    if not snippets:
        return ""
    lines = '<br>'.join(html.escape(s).replace("\n", "<br>") for s in snippets)
    extra = data.get("extra", "")
    out = ""
    if extra:
        out += f'<div class="cmd-description" style="margin-top:0.5rem">{esc(extra)}</div>'
    out += f'<div class="example-block">{lines}</div>'
    return out


def badge(params: list[dict]) -> str:
    required_args = [p for p in params if p.get("_kind") == "argument" and p["required"]]
    required_opts = [p for p in params if p.get("_kind") != "argument" and p["required"]]
    if required_args:
        names = ", ".join(p["name"].upper() for p in required_args)
        return f'<span class="cmd-badge badge-required">{esc(names)} required</span>'
    if required_opts:
        names = ", ".join(p["flags"].split(", ")[0] for p in required_opts)
        return f'<span class="cmd-badge badge-required">{esc(names)} required</span>'
    return '<span class="cmd-badge badge-optional">no required args</span>'


def command_card(path: str, cmd, examples_data: dict) -> str:
    params = collect_params(cmd)
    for p in params:
        # Mark kind after collection (we avoided refactor above)
        is_arg = p["name"] in [getattr(param, "name", "") for param in cmd.params if isinstance(param, click.Argument)]
        p["_kind"] = "argument" if is_arg else "option"

    usage = format_usage(path, params)
    brief = cmd.short_help or (cmd.help or "").split("\n")[0]
    search = build_search_tokens(path, cmd, params)

    card = f'''
      <div class="cmd-card" data-search="{search}">
        <div class="cmd-header" onclick="toggleCmd(this)">
          <span class="cmd-path">{esc(path)}</span>
          <span class="cmd-brief">{esc(brief)}</span>
          {badge(params)}
          <span class="cmd-expand">▶</span>
        </div>
        <div class="cmd-detail">
          <div class="cmd-description">{esc(cmd.help or brief)}</div>
          <div class="cmd-usage">{usage}</div>
          {param_table(params, path, examples_data)}
          {example_block(path, examples_data)}
        </div>
      </div>
'''
    return card


def panel_html(title: str, cmd_items: list[tuple[str, click.Command]], examples_data: dict) -> str:
    cards = [command_card(path, cmd, examples_data) for path, cmd in cmd_items]
    header = f'<div class="panel-title">{esc(title)}</div>' if title and title != "Commands" else ""
    return header + "".join(cards)


def category_html(title: str, icon: str, color: str, description: str, panels: list[tuple[str, list[tuple[str, click.Command]]]], examples_data: dict) -> str:
    bg, fg = COLOR_MAP.get(color, COLOR_MAP["accent"])
    panel_htmls = [panel_html(panel_title, cmds, examples_data) for panel_title, cmds in panels]
    body = "".join(panel_htmls)
    group_key = title.lower().replace(" & ", "-")
    return f'''
  <div class="category" data-group="{group_key}">
    <div class="category-header" onclick="toggleCategory(this)">
      <div class="category-icon" style="background:{bg};color:{fg};">{esc(icon)}</div>
      <div class="category-title">{esc(title)}</div>
      <div class="category-desc">{esc(description)}</div>
      <div class="category-toggle">▼</div>
    </div>
    <div class="category-body">
      {body}
    </div>
  </div>
'''


# ---------------------------------------------------------------------------
# Build section list from click.rich_click.COMMAND_GROUPS
# ---------------------------------------------------------------------------


def get_panels(path: str, group_cmd: click.Group, inherited_title: str | None = None) -> list[tuple[str, list[tuple[str, click.Command]]]]:
    """Return list of (panel_title, [(full_path, cmd), ...]) for a group, expanding nested groups."""
    groups = cli_module.click.rich_click.COMMAND_GROUPS.get(path, [])
    if not groups:
        cmds = [(f"{path} {name}".strip(), cmd) for name, cmd in group_cmd.commands.items()]
        return [(inherited_title or "Commands", cmds)]

    panels: list[tuple[str, list[tuple[str, click.Command]]]] = []
    for g in groups:
        title = g["name"]
        flat_items: list[tuple[str, click.Command]] = []
        for name in g.get("commands", []):
            sub = group_cmd.commands.get(name)
            if sub is None:
                continue
            child_path = f"{path} {name}".strip()
            if isinstance(sub, click.Group):
                nested = get_panels(child_path, sub, title)
                for nested_title, nested_items in nested:
                    combined = f"{title} — {nested_title}" if nested_title != "Commands" else title
                    panels.append((combined, nested_items))
            else:
                flat_items.append((child_path, sub))
        if flat_items:
            panels.append((title, flat_items))
    return panels


def build_sections() -> list[dict]:
    root_groups = cli_module.click.rich_click.COMMAND_GROUPS.get("artmind", [])
    sections = []
    for rg in root_groups:
        title = rg["name"]
        icon, color, description = CATEGORY_META.get(title, ("•", "accent", ""))
        panels: list[tuple[str, list[tuple[str, click.Command]]]] = []
        for name in rg.get("commands", []):
            cmd = cli.commands.get(name)
            if cmd is None:
                continue
            if isinstance(cmd, click.Group):
                panels.extend(get_panels(f"artmind {name}", cmd))
            else:
                panels.append(("Commands", [(f"artmind {name}", cmd)]))
        sections.append({
            "title": title,
            "icon": icon,
            "color": color,
            "description": description,
            "panels": panels,
        })
    return sections


# ---------------------------------------------------------------------------
# Full page template
# ---------------------------------------------------------------------------

PAGE_TEMPLATE = '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Artmind CLI — Interactive Guide</title>
<style>
  :root {{
    --bg: #0f1117;
    --surface: #161822;
    --surface2: #1c1f2e;
    --border: #2a2d3e;
    --text: #e2e4ea;
    --text-muted: #8b8fa3;
    --accent: #7c6aef;
    --accent-light: #9d8ff5;
    --accent-bg: rgba(124,106,239,0.08);
    --green: #4ade80;
    --green-bg: rgba(74,222,128,0.08);
    --blue: #60a5fa;
    --blue-bg: rgba(96,165,250,0.08);
    --orange: #fb923c;
    --orange-bg: rgba(251,146,60,0.08);
    --red: #f87171;
    --red-bg: rgba(248,113,113,0.08);
    --cyan: #22d3ee;
    --cyan-bg: rgba(34,211,238,0.08);
    --pink: #f472b6;
    --pink-bg: rgba(244,114,182,0.08);
    --yellow: #facc15;
    --yellow-bg: rgba(250,204,21,0.08);
    --radius: 10px;
  }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: 'SF Mono', 'Fira Code', 'JetBrains Mono', monospace; background: var(--bg); color: var(--text); line-height: 1.6; min-height: 100vh; }}
  .header {{ background: linear-gradient(135deg, var(--surface) 0%, var(--surface2) 100%); border-bottom: 1px solid var(--border); padding: 2rem 2rem 1.5rem; position: sticky; top: 0; z-index: 100; backdrop-filter: blur(12px); }}
  .header-top {{ display: flex; align-items: center; gap: 1rem; margin-bottom: 1rem; }}
  .logo {{ font-size: 1.5rem; font-weight: 800; background: linear-gradient(135deg, var(--accent), var(--cyan)); -webkit-background-clip: text; -webkit-text-fill-color: transparent; letter-spacing: -0.5px; }}
  .tagline {{ color: var(--text-muted); font-size: 0.8rem; }}
  .search-box {{ position: relative; }}
  .search-box input {{ width: 100%; padding: 0.7rem 1rem 0.7rem 2.5rem; background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius); color: var(--text); font-family: inherit; font-size: 0.85rem; outline: none; transition: border-color 0.2s; }}
  .search-box input:focus {{ border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-bg); }}
  .search-box::before {{ content: '⌕'; position: absolute; left: 0.8rem; top: 50%; transform: translateY(-50%); color: var(--text-muted); font-size: 1.1rem; }}
  .search-stats {{ font-size: 0.7rem; color: var(--text-muted); margin-top: 0.4rem; text-align: right; }}
  .main {{ max-width: 960px; margin: 0 auto; padding: 1.5rem 2rem 4rem; }}
  .category {{ margin-bottom: 2rem; }}
  .category-header {{ display: flex; align-items: center; gap: 0.6rem; padding: 0.6rem 0; margin-bottom: 0.5rem; cursor: pointer; user-select: none; border-bottom: 1px solid var(--border); transition: opacity 0.2s; }}
  .category-header:hover {{ opacity: 0.85; }}
  .category-icon {{ width: 28px; height: 28px; border-radius: 6px; display: flex; align-items: center; justify-content: center; font-size: 0.85rem; flex-shrink: 0; }}
  .category-title {{ font-size: 0.95rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; }}
  .category-desc {{ font-size: 0.75rem; color: var(--text-muted); margin-left: auto; text-transform: none; letter-spacing: 0; }}
  .category-toggle {{ margin-left: 0.5rem; color: var(--text-muted); transition: transform 0.2s; font-size: 0.7rem; }}
  .category.collapsed .category-toggle {{ transform: rotate(-90deg); }}
  .category.collapsed .category-body {{ display: none; }}
  .category-body {{ padding: 0.25rem 0; }}
  .panel-title {{ font-size: 0.8rem; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px; margin: 1rem 0 0.5rem; font-weight: 700; }}
  .cmd-card {{ background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); overflow: hidden; transition: border-color 0.2s, box-shadow 0.2s; margin-bottom: 0.5rem; }}
  .cmd-card:hover {{ border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent-bg); }}
  .cmd-card.hidden {{ display: none; }}
  .cmd-header {{ display: flex; align-items: center; gap: 0.75rem; padding: 0.75rem 1rem; cursor: pointer; user-select: none; }}
  .cmd-path {{ font-weight: 700; font-size: 0.82rem; color: var(--accent-light); }}
  .cmd-brief {{ color: var(--text-muted); font-size: 0.75rem; flex: 1; }}
  .cmd-badge {{ font-size: 0.6rem; padding: 0.15rem 0.5rem; border-radius: 20px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; flex-shrink: 0; }}
  .badge-required {{ background: var(--red-bg); color: var(--red); border: 1px solid rgba(248,113,113,0.2); }}
  .badge-optional {{ background: var(--green-bg); color: var(--green); border: 1px solid rgba(74,222,128,0.2); }}
  .cmd-expand {{ color: var(--text-muted); font-size: 0.65rem; transition: transform 0.2s; }}
  .cmd-card.open .cmd-expand {{ transform: rotate(90deg); }}
  .cmd-detail {{ display: none; padding: 0 1rem 1rem; border-top: 1px solid var(--border); }}
  .cmd-card.open .cmd-detail {{ display: block; }}
  .cmd-usage {{ background: var(--bg); border: 1px solid var(--border); border-radius: 6px; padding: 0.75rem 1rem; margin: 0.75rem 0; font-size: 0.78rem; color: var(--text); overflow-x: auto; }}
  .req {{ color: var(--red); font-weight: 700; }}
  .opt {{ color: var(--text-muted); }}
  .cmd-description {{ font-size: 0.78rem; color: var(--text-muted); margin-bottom: 0.75rem; line-height: 1.7; }}
  .cmd-options-title {{ font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.5px; color: var(--text-muted); margin-bottom: 0.5rem; font-weight: 600; }}
  .opt-table {{ width: 100%; border-collapse: collapse; font-size: 0.75rem; }}
  .opt-table td {{ padding: 0.4rem 0.6rem; border-bottom: 1px solid var(--border); vertical-align: top; }}
  .opt-table tr:last-child td {{ border-bottom: none; }}
  .opt-name {{ color: var(--cyan); font-weight: 600; white-space: nowrap; }}
  .opt-type {{ color: var(--text-muted); font-size: 0.65rem; }}
  .opt-required {{ color: var(--red); font-weight: 700; font-size: 0.65rem; }}
  .opt-default {{ color: var(--text-muted); font-size: 0.65rem; }}
  .example-block {{ background: var(--bg); border: 1px solid var(--border); border-radius: 6px; padding: 0.6rem 0.8rem; margin-top: 0.5rem; font-size: 0.75rem; color: var(--green); white-space: pre-line; }}
  .intro-card {{ background: linear-gradient(135deg, var(--surface), var(--surface2)); border: 1px solid var(--border); border-radius: var(--radius); padding: 1.5rem; margin-bottom: 2rem; }}
  .intro-card h2 {{ font-size: 1rem; margin-bottom: 0.75rem; background: linear-gradient(135deg, var(--accent-light), var(--cyan)); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }}
  .intro-card p {{ font-size: 0.78rem; color: var(--text-muted); line-height: 1.8; }}
  .intro-card .usage-line {{ background: var(--bg); border: 1px solid var(--border); border-radius: 6px; padding: 0.6rem 0.8rem; margin-top: 0.75rem; font-size: 0.82rem; }}
  .quickstart {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 0.75rem; margin-top: 1rem; }}
  .quickstart-step {{ background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 0.8rem; }}
  .step-num {{ display: inline-flex; align-items: center; justify-content: center; width: 22px; height: 22px; border-radius: 50%; background: var(--accent); color: white; font-size: 0.65rem; font-weight: 700; margin-bottom: 0.4rem; }}
  .step-title {{ font-size: 0.75rem; font-weight: 700; margin-bottom: 0.25rem; }}
  .step-cmd {{ font-size: 0.7rem; color: var(--green); }}
  .no-results {{ text-align: center; padding: 3rem; color: var(--text-muted); font-size: 0.85rem; display: none; }}
  .no-results.visible {{ display: block; }}
  .footer {{ text-align: center; padding: 2rem; color: var(--text-muted); font-size: 0.7rem; border-top: 1px solid var(--border); }}
  @media (max-width: 640px) {{ .header {{ padding: 1rem; }} .main {{ padding: 1rem; }} .category-desc {{ display: none; }} .opt-table {{ font-size: 0.7rem; }} }}
</style>
</head>
<body>
<div class="header">
  <div class="header-top">
    <div class="logo">artmind</div>
    <div class="tagline">CLI Reference Guide</div>
  </div>
  <div class="search-box">
    <input type="text" id="search" placeholder="Search commands, options, descriptions…" autocomplete="off" spellcheck="false">
  </div>
  <div class="search-stats" id="searchStats"></div>
</div>
<div class="main">
  <div class="intro-card">
    <h2>Artmind — A Knowledge System That Synchronizes with Your Mind</h2>
    <p>Artmind is a CLI tool for building, querying, and maintaining a knowledge graph backed by Neo4j and SQLite. It ingests documents, extracts entities and relationships via LLMs, and provides rich graph + vector queries.</p>
    <div class="usage-line"><span class="opt">Usage:</span> <span style="color:#9d8ff5">artmind</span> <span class="opt">[OPTIONS]</span> <span style="color:#9d8ff5">COMMAND</span> <span class="opt">[ARGS]...</span></div>
    <div class="quickstart">
      <div class="quickstart-step"><div class="step-num">1</div><div class="step-title">Initialize</div><div class="step-cmd">artmind setup</div></div>
      <div class="quickstart-step"><div class="step-num">2</div><div class="step-title">Add Domain Schema</div><div class="step-cmd">artmind domains add schema.yaml</div></div>
      <div class="quickstart-step"><div class="step-num">3</div><div class="step-title">Ingest Documents</div><div class="step-cmd">artmind ingest sync ./docs/</div></div>
      <div class="quickstart-step"><div class="step-num">4</div><div class="step-title">Query</div><div class="step-cmd">artmind query vector-text "question" --domain mydomain</div></div>
    </div>
  </div>
{body}
  <div class="no-results" id="noResults">
    No commands match your search. Try different keywords.
  </div>
</div>
<div class="footer">
  Generated from <code>artmind</code> CLI.
</div>
<script>
function toggleCategory(header) {{ header.closest('.category').classList.toggle('collapsed'); }}
function toggleCmd(header) {{ header.closest('.cmd-card').classList.toggle('open'); }}
const searchInput = document.getElementById('search');
const stats = document.getElementById('searchStats');
const noResults = document.getElementById('noResults');
const allCards = document.querySelectorAll('.cmd-card');
searchInput.addEventListener('input', function() {{
  const q = this.value.toLowerCase().trim();
  let visible = 0;
  allCards.forEach(card => {{
    const searchText = (card.getAttribute('data-search') + ' ' + card.textContent).toLowerCase();
    if (!q || searchText.includes(q)) {{ card.classList.remove('hidden'); visible++; }}
    else {{ card.classList.add('hidden'); card.classList.remove('open'); }}
  }});
  document.querySelectorAll('.category').forEach(cat => {{
    const hasVisible = cat.querySelectorAll('.cmd-card:not(.hidden)').length > 0;
    if (q) {{ cat.classList.toggle('collapsed', !hasVisible); }}
  }});
  noResults.classList.toggle('visible', q.length > 0 && visible === 0);
  stats.textContent = q ? `${{visible}} command${{visible !== 1 ? 's' : ''}} found` : '';
}});
document.addEventListener('keydown', e => {{
  if (e.key === '/' && document.activeElement !== searchInput) {{ e.preventDefault(); searchInput.focus(); }}
  if (e.key === 'Escape') {{ searchInput.value = ''; searchInput.dispatchEvent(new Event('input')); searchInput.blur(); }}
}});
</script>
</body>
</html>
'''


def generate_html() -> str:
    examples_data = load_examples()
    sections = build_sections()
    body = "".join(category_html(**s, examples_data=examples_data) for s in sections)
    return PAGE_TEMPLATE.format(body=body)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate artmind CLI HTML guide")
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_OUTPUT, help="Output HTML path")
    parser.add_argument("--check", action="store_true", help="Exit with non-zero if output would change")
    parser.add_argument("--examples", type=Path, default=EXAMPLES_FILE, help="JSON file with supplemental examples")
    args = parser.parse_args()

    output_path = args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)

    generated = generate_html()

    if args.check:
        current = output_path.read_text() if output_path.exists() else ""
        if generated != current:
            print(f"CLI guide is out of date: {output_path}", file=sys.stderr)
            print("Run: uv run python scripts/generate_cli_guide.py", file=sys.stderr)
            return 1
        print(f"CLI guide is up to date: {output_path}")
        return 0

    output_path.write_text(generated)
    print(f"Generated {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
