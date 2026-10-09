"""kg_views.render: golden markdown per format, tree edge cases, escaping."""

from artmind.kg_views.model import Presentation
from artmind.kg_views.render import render_markdown


def P(fmt: str, **opts) -> Presentation:
    return Presentation(format=fmt, options=opts)


def test_table_with_headers_and_escaping():
    rows = [
        {"regulation": "GDPR", "hops": 1},
        {"regulation": "A|B\nC", "hops": None},
    ]
    out = render_markdown(rows, P("table", columns=["regulation", "hops"], headers={"hops": "Distance"}))
    assert out == (
        "| regulation | Distance |\n"
        "| --- | --- |\n"
        "| GDPR | 1 |\n"
        "| A\\|B C |  |"
    )


def test_table_formats_lists_bools_and_dicts():
    rows = [{"a": ["x", "y"], "b": True, "c": {"k": 1}}]
    out = render_markdown(rows, P("table", columns=["a", "b", "c"]))
    assert out.splitlines()[2] == '| x, y | true | {"k": 1} |'


def test_list_with_and_without_detail():
    rows = [{"n": "GDPR", "d": "EU privacy"}, {"n": "SOX", "d": None}]
    assert render_markdown(rows, P("list", label="n", detail="d")) == (
        "- **GDPR** \u2014 EU privacy\n- **SOX**"
    )
    assert render_markdown(rows, P("list", label="n")) == "- **GDPR**\n- **SOX**"


def test_tree_nests_by_parent_and_treats_orphans_as_roots():
    rows = [
        {"id": "a", "parent": None, "name": "Root"},
        {"id": "b", "parent": "a", "name": "Child B"},
        {"id": "c", "parent": "b", "name": "Grandchild"},
        {"id": "d", "parent": "a", "name": "Child D"},
        {"id": "z", "parent": "missing", "name": "Orphan"},
    ]
    assert render_markdown(rows, P("tree", id="id", parent="parent", label="name")) == (
        "- Root\n  - Child B\n    - Grandchild\n  - Child D\n- Orphan"
    )


def test_tree_cycle_is_marked_not_expanded():
    rows = [
        {"id": "a", "parent": "b", "name": "A"},
        {"id": "b", "parent": "a", "name": "B"},
    ]
    out = render_markdown(rows, P("tree", id="id", parent="parent", label="name"))
    assert out == "- A\n  - B\n    - A \u21bb (see above)"


def test_tree_second_parent_is_marked_not_expanded():
    rows = [
        {"id": "a", "parent": None, "name": "A"},
        {"id": "b", "parent": None, "name": "B"},
        {"id": "x", "parent": "a", "name": "X"},
        {"id": "x", "parent": "b", "name": "X"},
        {"id": "y", "parent": "x", "name": "Y"},
    ]
    out = render_markdown(rows, P("tree", id="id", parent="parent", label="name"))
    assert out == "- A\n  - X\n    - Y\n- B\n  - X \u21bb (see above)"


def test_mermaid_graph_stable_ids_and_escaping():
    rows = [
        {"s": "Acme", "t": 'He said "hi" [x]', "e": "owns|controls"},
        {"s": "Acme", "t": "Beta", "e": None},
        {"s": None, "t": "Gamma", "e": "skipped"},
    ]
    out = render_markdown(rows, P("mermaid_graph", source="s", target="t", edge_label="e"))
    assert out == (
        "```mermaid\n"
        "graph LR\n"
        '  n0["Acme"]\n'
        '  n1["He said #quot;hi#quot; #91;x#93;"]\n'
        '  n2["Beta"]\n'
        '  n0 -->|"owns#124;controls"| n1\n'
        "  n0 --> n2\n"
        "```"
    )


def test_mermaid_flow_header():
    out = render_markdown([{"s": "a", "t": "b"}], P("mermaid_flow", source="s", target="t"))
    assert out.splitlines()[1] == "flowchart TD"


def test_truncation_footer_and_empty():
    rows = [{"n": "x"}]
    out = render_markdown(rows, P("list", label="n"), truncated=True, rows_total=40)
    assert out.endswith("\n\n_Showing 1 of 40 rows._")
    assert render_markdown([], P("list", label="n")) == "_No rows._"
    assert "Showing" not in render_markdown(rows, P("list", label="n"), truncated=False, rows_total=1)
