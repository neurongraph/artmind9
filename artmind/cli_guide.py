"""Render the artmind Click CLI as an HTML fragment for the admin-ui's CLI guide tab.

Output is a **fragment, not a page**: bare markup with `cg-`-prefixed classes,
styled entirely by `webui/static/dashboard.css` and themed by the admin-ui's
own CSS variables. It carries no `<style>`, no `<script>`, no palette of its
own — so it follows the dashboard's light/dark toggle for free and can't
collide with the surrounding page.

Consumed by `GET /api/cli-guide` (see `webui/dashboard_routes.py`), which
regenerates it per request from this process's live `artmind.cli` import. That
means it always matches the running command tree — though, like the `serve`
daemon, an already-running admin-ui won't reflect `cli.py` edits until it is
restarted (see CLAUDE.md "Testing implications").

The interactive bits (expand/collapse, search) live in `dashboard.js`; the
search box and result counter are static chrome in `dashboard.html`. Nothing
here emits behaviour.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

# Import artmind's CLI. In artmind/cli.py Click is aliased as `click`.
import artmind.cli as cli_module

click = cli_module.click
cli = cli_module.cli

PACKAGE_DIR = Path(__file__).resolve().parent
EXAMPLES_FILE = PACKAGE_DIR / "cli_guide_examples.json"


# ---------------------------------------------------------------------------
# Category glyph + blurb. Ordering (and membership) comes from
# click.rich_click.COMMAND_GROUPS in cli.py -- this only decorates it.
#
# Deliberately no per-category colour: the admin-ui is monochrome-plus-accent,
# so the glyph carries the distinction and every category tints from
# var(--accent). A rainbow here is what made the old standalone page read as a
# foreign document embedded in the dashboard.
# ---------------------------------------------------------------------------

CATEGORY_META: dict[str, tuple[str, str]] = {
    "Setup & tools": ("⚙", "Initialize tables and constraints"),
    "Domains": ("◆", "Manage domain schemas and hierarchy"),
    "Ingestion": ("↓", "Document ingestion, graph building, and refinement"),
    "Structured store": ("▤", "SQL tables bridged to the graph — mappings, grain, catalogue"),
    "Query": ("?", "Knowledge graph and vector index queries"),
    "Documents": ("□", "Manage ingested documents"),
    "Projection": ("▣", "Recompute Entities from observations; synthesize descriptions"),
    "Curation": ("◇", "Curated same-as groups — the review queue and same_as.yaml"),
    "Updates": ("✎", "Add and update facts from natural language"),
    "Sessions": ("◉", "Save and restore the Neo4j graph between sessions"),
}


def load_examples(path: Path | None = None) -> dict[str, dict]:
    path = path or EXAMPLES_FILE
    if path.exists():
        return json.loads(path.read_text())
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
    """Arguments first, then options -- each tagged with its kind."""
    args: list[dict] = []
    opts: list[dict] = []
    for param in cmd.params:
        is_argument = isinstance(param, click.Argument)
        info = {
            "name": param.name,
            "flags": format_flags(param),
            "type": type_name(param),
            "required": bool(getattr(param, "required", False)),
            "default": format_default(param),
            "multiple": bool(getattr(param, "multiple", False)),
            "is_flag": bool(getattr(param, "is_flag", False)),
            "help": getattr(param, "help", "") or "",
            "kind": "argument" if is_argument else "option",
        }
        (args if is_argument else opts).append(info)
    return args + opts


def format_usage(path: str, params: list[dict]) -> str:
    """Build a usage line like: artmind domains add DOMAIN_FILE [OPTIONS]"""
    parts = [html.escape(path)]
    for p in (p for p in params if p["kind"] == "argument"):
        token = p["name"].upper()
        if p["multiple"]:
            token += "..."
        token = html.escape(token)
        if p["required"]:
            parts.append(f'<span class="cg-req">{token}</span>')
        else:
            parts.append(f'<span class="cg-muted">[{token}]</span>')
    if any(p["kind"] == "option" for p in params):
        parts.append('<span class="cg-muted">[OPTIONS]</span>')
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
# Markup
# ---------------------------------------------------------------------------


def esc(text: str) -> str:
    text = str(text).replace("\\b", "").replace("\b", "")
    text = html.escape(text)
    return text.replace("\n", "<br>")


def param_table(params: list[dict]) -> str:
    if not params:
        return ""
    rows = []
    for p in params:
        if p["kind"] == "argument":
            name_cell = f'<span class="cg-arg-name">{esc(p["name"].upper())}</span>'
        else:
            name_cell = f'<span class="cg-opt-name">{esc(p["flags"])}</span>'
        tags = []
        if p["required"]:
            tags.append('<span class="cg-tag cg-tag-req">required</span>')
        if p["default"] is not None:
            tags.append(f'<span class="cg-tag">default: {esc(p["default"])}</span>')
        if p["multiple"]:
            tags.append('<span class="cg-tag">repeatable</span>')
        rows.append(
            f"<tr><td>{name_cell}<br><span class='cg-type'>{esc(p['type'])}</span></td>"
            f"<td>{esc(p['help'])}</td><td>{' '.join(tags)}</td></tr>"
        )
    return (
        '<div class="cg-subhead">Options &amp; arguments</div>'
        f'<div class="table-scroll"><table class="cg-table">{"".join(rows)}</table></div>'
    )


def example_block(path: str, examples_data: dict) -> str:
    data = examples_data.get(path, {})
    snippets = data.get("examples", [])
    if not snippets:
        return ""
    lines = "<br>".join(html.escape(s).replace("\n", "<br>") for s in snippets)
    out = ""
    if data.get("extra"):
        out += f'<p class="cg-desc">{esc(data["extra"])}</p>'
    return out + f'<div class="cg-example">{lines}</div>'


def badge(params: list[dict]) -> str:
    required_args = [p for p in params if p["kind"] == "argument" and p["required"]]
    required_opts = [p for p in params if p["kind"] == "option" and p["required"]]
    if required_args:
        names = ", ".join(p["name"].upper() for p in required_args)
        return f'<span class="cg-badge cg-badge-req">{esc(names)} required</span>'
    if required_opts:
        names = ", ".join(p["flags"].split(", ")[0] for p in required_opts)
        return f'<span class="cg-badge cg-badge-req">{esc(names)} required</span>'
    return '<span class="cg-badge">no required args</span>'


def command_card(path: str, cmd, examples_data: dict) -> str:
    params = collect_params(cmd)
    brief = cmd.short_help or (cmd.help or "").split("\n")[0]

    # Click derives short_help from the first line of help, so rendering both
    # the header brief and the full help repeats that line in every card with
    # a multi-line docstring. Keep only what the brief hasn't already said.
    detail = (cmd.help or "").strip()
    if detail.startswith(brief):
        detail = detail[len(brief):].strip()
    detail_html = f'<p class="cg-desc">{esc(detail)}</p>' if detail else ""

    return f'''
      <div class="cg-card" data-search="{build_search_tokens(path, cmd, params)}">
        <button type="button" class="cg-card-head">
          <span class="cg-caret" aria-hidden="true">▶</span>
          <span class="cg-path">{esc(path)}</span>
          <span class="cg-brief">{esc(brief)}</span>
          {badge(params)}
        </button>
        <div class="cg-card-body">
          {detail_html}
          <div class="cg-usage">{format_usage(path, params)}</div>
          {param_table(params)}
          {example_block(path, examples_data)}
        </div>
      </div>
'''


def panel_html(title: str, cmd_items: list[tuple[str, click.Command]], examples_data: dict) -> str:
    cards = "".join(command_card(path, cmd, examples_data) for path, cmd in cmd_items)
    header = f'<div class="cg-panel-title">{esc(title)}</div>' if title and title != "Commands" else ""
    return header + cards


def category_html(title: str, icon: str, description: str, panels, examples_data: dict) -> str:
    body = "".join(panel_html(t, cmds, examples_data) for t, cmds in panels)
    return f'''
  <section class="cg-category" data-group="{esc(title.lower().replace(" & ", "-"))}">
    <button type="button" class="cg-cat-head">
      <span class="cg-cat-icon" aria-hidden="true">{esc(icon)}</span>
      <span class="cg-cat-title">{esc(title)}</span>
      <span class="cg-cat-desc">{esc(description)}</span>
      <span class="cg-cat-toggle" aria-hidden="true">▼</span>
    </button>
    <div class="cg-cat-body">{body}</div>
  </section>
'''


# ---------------------------------------------------------------------------
# Build section list from click.rich_click.COMMAND_GROUPS
# ---------------------------------------------------------------------------


def get_panels(path: str, group_cmd: click.Group, inherited_title: str | None = None):
    """Return [(panel_title, [(full_path, cmd), ...]), ...] for a group, expanding nested groups."""
    groups = cli_module.click.rich_click.COMMAND_GROUPS.get(path, [])
    if not groups:
        cmds = [(f"{path} {name}".strip(), cmd) for name, cmd in group_cmd.commands.items()]
        return [(inherited_title or "Commands", cmds)]

    panels: list[tuple[str, list[tuple[str, click.Command]]]] = []
    for g in groups:
        title = g["name"]
        flat_items: list[tuple[str, click.Command]] = []
        nested_panels: list[tuple[str, list[tuple[str, click.Command]]]] = []
        for name in g.get("commands", []):
            sub = group_cmd.commands.get(name)
            if sub is None:
                continue
            child_path = f"{path} {name}".strip()
            if not isinstance(sub, click.Group):
                flat_items.append((child_path, sub))
                continue
            # `invoke_without_command=True` declares a group that is itself
            # runnable -- `db mappings TABLE` lists mappings, and carries
            # options (--acceptProposed) that appear on no subcommand. It
            # earns its own card, ahead of the subcommands it namespaces.
            if sub.invoke_without_command:
                flat_items.append((child_path, sub))
            for nested_title, nested_items in get_panels(child_path, sub, title):
                # A nested group with no COMMAND_GROUPS entry of its own inherits
                # this panel's title, so it belongs in this panel rather than
                # under a heading that repeats itself ("Query — Query").
                if nested_title in ("Commands", title):
                    flat_items.extend(nested_items)
                else:
                    nested_panels.append((f"{title} — {nested_title}", nested_items))
        if flat_items:
            panels.append((title, flat_items))
        panels.extend(nested_panels)
    return panels


def build_sections() -> list[dict]:
    sections = []
    for rg in cli_module.click.rich_click.COMMAND_GROUPS.get("artmind", []):
        title = rg["name"]
        icon, description = CATEGORY_META.get(title, ("•", ""))
        panels: list[tuple[str, list[tuple[str, click.Command]]]] = []
        for name in rg.get("commands", []):
            cmd = cli.commands.get(name)
            if cmd is None:
                continue
            if isinstance(cmd, click.Group):
                group_panels = get_panels(f"artmind {name}", cmd)
                # A TOP-LEVEL group declared `invoke_without_command=True` is a
                # command in its own right -- `artmind workspace` reports the
                # active workspace. get_panels() handles this for nested groups
                # only (see its `db mappings` comment), so without this a
                # top-level one would be routed by COMMAND_GROUPS yet still have
                # no card: silently undocumented, the exact failure the nested
                # case already guards against.
                if cmd.invoke_without_command:
                    group_panels.insert(0, ("Commands", [(f"artmind {name}", cmd)]))
                panels.extend(group_panels)
            else:
                panels.append(("Commands", [(f"artmind {name}", cmd)]))
        sections.append({
            "title": title,
            "icon": icon,
            "description": description,
            "panels": panels,
        })
    return sections


def render_fragment(sections: list[dict] | None = None, examples_path: Path | None = None) -> str:
    """The CLI guide as bare markup — no <style>, no <script>, no wrapper page."""
    examples_data = load_examples(examples_path)
    if sections is None:
        sections = build_sections()
    return "".join(category_html(**s, examples_data=examples_data) for s in sections)


# ---------------------------------------------------------------------------
# Coverage check — catches commands that COMMAND_GROUPS doesn't route anywhere,
# which would otherwise vanish from the guide silently (build_sections() only
# ever walks paths reachable from click.rich_click.COMMAND_GROUPS).
# Asserted by test/test_cli_guide.py.
# ---------------------------------------------------------------------------


def collect_leaf_paths(cmd: click.Command, path: str) -> set[str]:
    """Every path a user can actually run.

    A group is normally just a namespace, but one declared
    `invoke_without_command=True` is runnable in its own right and counts as a
    command too -- otherwise it could quietly go undocumented forever, which is
    exactly what happened to `artmind db mappings`.
    """
    if isinstance(cmd, click.Group):
        paths: set[str] = {path} if cmd.invoke_without_command else set()
        for name, sub in cmd.commands.items():
            paths |= collect_leaf_paths(sub, f"{path} {name}")
        return paths
    return {path}


def collect_documented_paths(sections: list[dict]) -> set[str]:
    paths: set[str] = set()
    for section in sections:
        for _, cmd_items in section["panels"]:
            paths.update(p for p, _ in cmd_items)
    return paths


def find_undocumented_commands(sections: list[dict] | None = None) -> list[str]:
    """Commands click can run but that COMMAND_GROUPS doesn't route into the guide."""
    sections = build_sections() if sections is None else sections
    return sorted(collect_leaf_paths(cli, "artmind") - collect_documented_paths(sections))
