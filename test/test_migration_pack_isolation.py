"""No module outside the migration pack imports it (plan Part 9): one `add_command` line only."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "artmind"
PACK = ROOT / "migrations" / "temporal_v1"


def test_only_one_add_command_line_names_the_pack():
    hits = []
    for path in ROOT.rglob("*.py"):
        if PACK in path.parents:
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "temporal_v1" in line:
                hits.append((path.relative_to(ROOT).as_posix(), line.strip()))
    assert len(hits) == 1, hits
    assert hits[0][0] == "cli.py" and "from artmind.migrations.temporal_v1.cli import migrate_group" in hits[0][1]
    assert (ROOT / "cli.py").read_text(encoding="utf-8").count("cli.add_command(migrate_group)") == 1
