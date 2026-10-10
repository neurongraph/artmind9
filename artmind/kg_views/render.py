"""Deterministic markdown rendering of a view's rows (spec section 7, Renderer).

Pure: rows in (list of dicts), markdown out. No Neo4j, no filesystem.
"""

from __future__ import annotations

import json
from typing import Any

from artmind.kg_views.model import Presentation


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return ", ".join(_cell(v) for v in value)
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    return str(value)


def _table_cell(value: Any) -> str:
    return _cell(value).replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _render_table(rows: list[dict], opts: dict) -> str:
    columns = opts["columns"]
    headers = opts.get("headers") or {}
    lines = [
        "| " + " | ".join(_table_cell(headers.get(c, c)) for c in columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(_table_cell(row.get(c)) for c in columns) + " |")
    return "\n".join(lines)


def _line(value: Any) -> str:
    return _cell(value).replace("\r", " ").replace("\n", " ")


def _render_list(rows: list[dict], opts: dict) -> str:
    label_col, detail_col = opts["label"], opts.get("detail")
    lines = []
    for row in rows:
        line = f"- **{_line(row.get(label_col))}**"
        detail = _line(row.get(detail_col)) if detail_col else ""
        if detail:
            line += f" \u2014 {detail}"
        lines.append(line)
    return "\n".join(lines)


def _render_tree(rows: list[dict], opts: dict) -> str:
    id_col, parent_col, label_col = opts["id"], opts["parent"], opts["label"]
    labels: dict[str, str] = {}
    order: list[str] = []
    children: dict[str, list[str]] = {}
    edges: list[tuple[str | None, str]] = []
    for row in rows:
        node = row.get(id_col)
        if node is None:
            continue
        node = _cell(node)
        if node not in labels:
            labels[node] = _line(row.get(label_col))
            order.append(node)
        parent = row.get(parent_col)
        edges.append((None if parent is None else _cell(parent), node))
    has_parent: set[str] = set()
    seen_edges: set[tuple[str, str]] = set()
    for parent, node in edges:
        if parent is not None and parent in labels and (parent, node) not in seen_edges:
            seen_edges.add((parent, node))
            children.setdefault(parent, []).append(node)
            has_parent.add(node)
    roots = [n for n in order if n not in has_parent]
    lines: list[str] = []
    seen: set[str] = set()

    def walk(start: str) -> None:
        stack = [(start, 0)]
        while stack:
            node, depth = stack.pop()
            indent = "  " * depth
            if node in seen:
                lines.append(f"{indent}- {labels[node]} \u21bb (see above)")
                continue
            seen.add(node)
            lines.append(f"{indent}- {labels[node]}")
            for child in reversed(children.get(node, [])):
                stack.append((child, depth + 1))

    for root in roots:
        walk(root)
    for node in order:  # nodes only reachable through a cycle have no root
        if node not in seen:
            walk(node)
    return "\n".join(lines)


def _mermaid_text(text: str) -> str:
    for char, code in (("#", "#35;"), ('"', "#quot;"), ("[", "#91;"), ("]", "#93;"), ("|", "#124;")):
        text = text.replace(char, code)
    return text.replace("\r", " ").replace("\n", " ")


def _render_mermaid(rows: list[dict], opts: dict, header: str) -> str:
    source_col, target_col, edge_col = opts["source"], opts["target"], opts.get("edge_label")
    ids: dict[str, str] = {}
    nodes: list[str] = []
    edges: list[str] = []

    def node_id(value: Any) -> str:
        key = _cell(value)
        if key not in ids:
            ids[key] = f"n{len(ids)}"
            nodes.append(f'  {ids[key]}["{_mermaid_text(key)}"]')
        return ids[key]

    for row in rows:
        if row.get(source_col) is None or row.get(target_col) is None:
            continue
        src, dst = node_id(row[source_col]), node_id(row[target_col])
        label = _cell(row.get(edge_col)) if edge_col else ""
        arrow = f'-->|"{_mermaid_text(label)}"|' if label else "-->"
        edges.append(f"  {src} {arrow} {dst}")
    body = "\n".join([header, *nodes, *edges])
    return f"```mermaid\n{body}\n```"


def render_markdown(
    rows: list[dict],
    presentation: Presentation,
    truncated: bool = False,
    rows_total: int | None = None,
) -> str:
    if not rows:
        return "_No rows._"
    fmt, opts = presentation.format, presentation.options
    if fmt == "table":
        out = _render_table(rows, opts)
    elif fmt == "list":
        out = _render_list(rows, opts)
    elif fmt == "tree":
        out = _render_tree(rows, opts)
    elif fmt == "mermaid_graph":
        out = _render_mermaid(rows, opts, "graph LR")
    elif fmt == "mermaid_flow":
        out = _render_mermaid(rows, opts, "flowchart TD")
    else:
        raise ValueError(f"unknown presentation format {fmt!r}")
    if truncated and rows_total is not None:
        out += f"\n\n_Showing {len(rows)} of {rows_total} rows._"
    return out
