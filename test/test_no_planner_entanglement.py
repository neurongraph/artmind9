"""No new code depends on the frozen planner (spec 2026-11-08 §2.3, D2). The seams below existed at v0.11.0;
phase 3 removes them. This list may only shrink."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "artmind"
SEAMS = {
    "artmind/cli.py",
    "artmind/server.py",
    "artmind/webui/app.py",
    "artmind/traces/recorder.py",
    "artmind/catalogue/cache.py",
}


def _imports_planner(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("artmind.query_plan"):
            return True
        if isinstance(node, ast.Import) and any(a.name.startswith("artmind.query_plan") for a in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and node.module == "artmind" and any(
                a.name == "query_plan" for a in node.names):
            return True
    return False


def _found() -> set[str]:
    return {str(p.relative_to(ROOT.parent)) for p in ROOT.rglob("*.py")
            if "query_plan" not in p.relative_to(ROOT).parts and _imports_planner(p)}


def test_only_the_pinned_seams_import_the_planner():
    found = _found()
    assert found <= SEAMS, f"new planner imports: {sorted(found - SEAMS)}"


def test_the_seam_list_only_shrinks():
    found = _found()
    assert SEAMS <= found, f"remove these from SEAMS, they no longer import the planner: {sorted(SEAMS - found)}"
