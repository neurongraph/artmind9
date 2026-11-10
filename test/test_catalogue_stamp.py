"""Disk stamps (spec 2026-11-02 §3): add, edit, rename and delete each change one."""
import os

from artmind.catalogue import stamp


def test_missing_or_empty_directory_is_the_empty_stamp(tmp_path):
    assert stamp.stamp(tmp_path / "nope") == stamp.EMPTY
    assert stamp.stamp(None) == stamp.EMPTY
    assert stamp.stamp(tmp_path) == stamp.EMPTY


def test_an_untouched_tree_keeps_its_stamp(tmp_path):
    (tmp_path / "a.md").write_text("one")
    assert stamp.stamp(tmp_path) == stamp.stamp(tmp_path)


def test_add_edit_rename_and_delete_each_change_it(tmp_path):
    f = tmp_path / "a.md"
    f.write_text("one")
    seen = [stamp.stamp(tmp_path)]

    (tmp_path / "b.md").write_text("two")  # add
    seen.append(stamp.stamp(tmp_path))

    os.utime(f, ns=(1, f.stat().st_mtime_ns + 5_000_000_000))  # edit: only the mtime moves
    seen.append(stamp.stamp(tmp_path))

    f.rename(tmp_path / "c.md")  # rename: same count, new name
    seen.append(stamp.stamp(tmp_path))

    (tmp_path / "b.md").unlink()  # delete
    seen.append(stamp.stamp(tmp_path))

    assert len(set(seen)) == len(seen)


def test_a_rename_with_the_same_count_and_mtime_changes_the_name_hash(tmp_path):
    f = tmp_path / "a.md"
    f.write_text("one")
    before = stamp.stamp(tmp_path)
    mtime = f.stat().st_mtime_ns
    f.rename(tmp_path / "z.md")
    os.utime(tmp_path / "z.md", ns=(mtime, mtime))
    after = stamp.stamp(tmp_path)
    assert before[:2] == after[:2] and before[2] != after[2]


def test_suffix_filter_and_hidden_files(tmp_path):
    (tmp_path / "page.md").write_text("x")
    before = stamp.stamp(tmp_path, suffix=".md")
    (tmp_path / "notes.txt").write_text("y")
    (tmp_path / ".tmp-page.md").write_text("z")  # a half-written temp file
    assert stamp.stamp(tmp_path, suffix=".md") == before


def test_disk_stamps_cover_views_plans_insights_and_schemas(tmp_path, monkeypatch):
    import paths

    views, topics, insights, schemas = (tmp_path / n for n in ("views", "topics", "insights", "schemas"))
    for d in (views, topics, insights / "journal", schemas):
        d.mkdir(parents=True)
    monkeypatch.setattr(paths, "DOMAIN_VIEWS_DIR", views)
    monkeypatch.setattr(paths, "DOMAIN_TOPIC_PLANS_DIR", topics)
    monkeypatch.setattr(paths, "INSIGHTS_DIR", insights)
    monkeypatch.setattr(paths, "DOMAIN_SCHEMAS_DIR", schemas)
    base = stamp.disk_stamp("journal")
    assert set(stamp.disk_stamps("journal")) == {"views", "plans", "insights", "schemas"}

    (topics / "p").mkdir()
    (topics / "p" / "plan.yaml").write_text("x")
    after_topic = stamp.disk_stamp("journal")
    assert after_topic != base
    (insights / "other").mkdir()
    (insights / "other" / "run.json").write_text("{}")  # another domain's snapshot
    assert stamp.disk_stamp("journal") == after_topic
    (insights / "journal" / "run.json").write_text("{}")
    assert stamp.disk_stamp("journal") != after_topic
    assert isinstance(stamp.disk_stamp("journal"), str)  # survives the JSON cache round trip
