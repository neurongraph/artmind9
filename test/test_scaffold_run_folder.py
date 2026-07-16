"""Run-folder seeding policy: package assets refresh, user data is preserved.

The distinction matters because the chat agent runs with cwd set to the run
folder, so it reads the seeded *copies* of the skills — not the package. When
seeding skipped entries that already existed, a fixed skill stayed broken there
indefinitely and no reinstall could dislodge it.
"""

from artmind.setup import _seed_tree


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


# ── package assets (overwrite=True) ───────────────────────────────────────────


def test_package_asset_edit_reaches_an_existing_dest(tmp_path):
    """The regression: an edited skill must land even though the name exists."""
    src, dest = tmp_path / "pkg", tmp_path / "run"
    _write(src / "artmind-query" / "SKILL.md", "artmind query graph pattern10")
    _write(dest / "artmind-query" / "SKILL.md", "pattern10")  # stale seeded copy

    assert _seed_tree(src, dest, overwrite=True) == 1
    assert (dest / "artmind-query" / "SKILL.md").read_text() == "artmind query graph pattern10"


def test_overwrite_replaces_wholesale_so_removed_files_disappear(tmp_path):
    src, dest = tmp_path / "pkg", tmp_path / "run"
    _write(src / "skill" / "SKILL.md", "current")
    _write(dest / "skill" / "SKILL.md", "old")
    _write(dest / "skill" / "reference.md", "dropped from the package")

    _seed_tree(src, dest, overwrite=True)

    assert (dest / "skill" / "SKILL.md").read_text() == "current"
    assert not (dest / "skill" / "reference.md").exists()


def test_overwrite_leaves_names_the_package_does_not_ship(tmp_path):
    """A user's own skill in the run folder is not pruned."""
    src, dest = tmp_path / "pkg", tmp_path / "run"
    _write(src / "shipped" / "SKILL.md", "shipped")
    _write(dest / "mine" / "SKILL.md", "hand-written")

    _seed_tree(src, dest, overwrite=True)

    assert (dest / "mine" / "SKILL.md").read_text() == "hand-written"
    assert (dest / "shipped" / "SKILL.md").exists()


def test_overwrite_handles_single_files(tmp_path):
    """opencode seeds a file tree, not only directories."""
    src, dest = tmp_path / "pkg", tmp_path / "run"
    _write(src / "agent.md", "new persona")
    _write(dest / "agent.md", "old persona")

    assert _seed_tree(src, dest, overwrite=True) == 1
    assert (dest / "agent.md").read_text() == "new persona"


# ── user data (overwrite=False) ───────────────────────────────────────────────


def test_user_edits_survive_when_not_overwriting(tmp_path):
    src, dest = tmp_path / "pkg", tmp_path / "run"
    _write(src / "banking.yaml", "shipped default")
    _write(dest / "banking.yaml", "locally tuned")

    assert _seed_tree(src, dest) == 0
    assert (dest / "banking.yaml").read_text() == "locally tuned"


def test_new_entries_are_seeded_either_way(tmp_path):
    src, dest = tmp_path / "pkg", tmp_path / "run"
    _write(src / "fresh.yaml", "shipped")
    dest.mkdir()

    assert _seed_tree(src, dest) == 1
    assert (dest / "fresh.yaml").read_text() == "shipped"


# ── shared behaviour ──────────────────────────────────────────────────────────


def test_missing_source_is_a_noop(tmp_path):
    assert _seed_tree(tmp_path / "absent", tmp_path / "run", overwrite=True) == 0


def test_ds_store_is_never_seeded(tmp_path):
    src, dest = tmp_path / "pkg", tmp_path / "run"
    _write(src / ".DS_Store", "junk")
    _write(src / "skill" / "SKILL.md", "real")

    assert _seed_tree(src, dest, overwrite=True) == 1
    assert not (dest / ".DS_Store").exists()
