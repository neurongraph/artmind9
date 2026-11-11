"""guide keys ↔ reference files ↔ routing table (spec 2026-11-11 skill progressive disclosure §5.1, §6)."""
import ast
import re
from pathlib import Path

from artmind import guides

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "artmind"
# The skill that ships the routing table and guide keys. Task 11 changes this to artmind-query.
SKILL_DIR = PACKAGE / "skills" / "artmind-query-p2b"
REFS = SKILL_DIR / "references"
SKILL = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")


def _routing_refs() -> set[str]:
    table = SKILL.split("| Question shape | First command | Reference |", 1)[1].split("\n\n", 1)[0]
    return set(re.findall(r"`([a-z-]+)\.md`", table))


def test_every_key_has_its_reference():
    missing = [k for k in guides.GUIDES if not (REFS / f"{k}.md").is_file()]
    assert not missing, missing


def test_every_routing_reference_exists():
    missing = sorted(r for r in _routing_refs() if not (REFS / f"{r}.md").is_file())
    assert not missing, missing


def test_every_reference_is_reached():
    reached = set(guides.GUIDES) | _routing_refs() | {"commands"}
    orphans = sorted(p.stem for p in REFS.glob("*.md") if p.stem not in reached)
    assert not orphans, orphans


# Files that write a `guide` key that is not a result's: span evidence records the result's guide.
ALLOWED = {"artmind/guides.py", "artmind/traces/evidence.py"}


def test_guide_is_set_only_in_guides_py():
    offenders = []
    for path in PACKAGE.rglob("*.py"):
        if str(path.relative_to(ROOT)) in ALLOWED or "skills" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store) \
                    and isinstance(node.slice, ast.Constant) and node.slice.value == "guide":
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
            if isinstance(node, ast.Dict) and any(isinstance(k, ast.Constant) and k.value == "guide" for k in node.keys):
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert not offenders, "set `guide` only through artmind.guides: " + ", ".join(offenders)
