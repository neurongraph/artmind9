"""Text-level frontmatter edits (spec 2026-09-26 §5 R6): only the lines
artmind must change move; the body, the user's keys, their order, comments,
quoting and line endings stay byte-for-byte."""
import os
import stat

import pytest

from artmind import frontmatter as fm
from artmind.ingest import _parse_md_frontmatter

USER_BLOCK = (
    "---\n"
    'title: "My note"   # my own comment\n'
    "tags:\n"
    "- alpha\n"
    "- beta\n"
    "aliases: [x, y]\n"
    "---\n"
)
BODY = "\n# Heading\n\nBody text -- with trailing spaces   \n"


def test_a_note_without_frontmatter_gets_a_block_and_keeps_every_byte():
    text = "# Heading\n\nBody.\n"

    new = fm.set_fields(text, {"_artmind_id": "0192-id", "_domain": "banking.ops"})

    assert new == "---\n_artmind_id: 0192-id\n_domain: banking.ops\n---\n" + text
    meta, body = _parse_md_frontmatter(new)
    assert meta == {"_artmind_id": "0192-id", "_domain": "banking.ops"}
    assert body == text


def test_a_crlf_note_without_frontmatter_gets_crlf_lines():
    text = "# Heading\r\n\r\nBody.\r\n"

    new = fm.set_fields(text, {"_artmind_id": "id", "_domain": "d"})

    assert new == "---\r\n_artmind_id: id\r\n_domain: d\r\n---\r\n" + text


def test_stamping_keeps_the_users_keys_their_order_comments_and_quoting():
    text = USER_BLOCK + BODY

    new = fm.set_fields(text, {"_artmind_id": "id-1", "_domain": "general"})

    assert new == (
        "---\n"
        "_artmind_id: id-1\n"
        "_domain: general\n"
        'title: "My note"   # my own comment\n'
        "tags:\n"
        "- alpha\n"
        "- beta\n"
        "aliases: [x, y]\n"
        "---\n"
    ) + BODY


def test_a_domain_change_rewrites_only_that_line_in_place():
    text = "---\ntitle: T\n_domain: general\n_artmind_id: id-1\nextra: 1\n---\nBody\n"

    new = fm.set_fields(text, {"_artmind_id": "id-1", "_domain": "banking.ops"})

    assert new == "---\ntitle: T\n_domain: banking.ops\n_artmind_id: id-1\nextra: 1\n---\nBody\n"


def test_a_value_that_is_already_right_is_not_rewritten():
    text = "---\n_artmind_id: 'id-1'\n_domain: \"general\"\n---\nBody\n"

    assert fm.set_fields(text, {"_artmind_id": "id-1", "_domain": "general"}) == text


def test_remove_fields_drops_the_fat_block_and_nothing_else_crlf_preserved():
    long_path = "notes/" + "very-long-folder-name/" * 5 + "note.md"
    text = (
        "---\r\n"
        "_artmind_id: id-1\r\n"
        "_version: 3\r\n"
        "_content_sha256: " + "a" * 64 + "\r\n"
        "_domain: general\r\n"
        "_status: latest\r\n"
        "_source_commit: " + "b" * 40 + "\r\n"
        "_source_path: " + long_path[:60] + "\r\n"
        "  " + long_path[60:] + "\r\n"
        "_source_type: md\r\n"
        "_ingested_at: '2026-09-01T10:00:00+00:00'\r\n"
        "title: Mine\r\n"
        "tags:\r\n"
        "- a\r\n"
        "---\r\n"
        "\r\nBody\r\n"
    )
    moved = ("_version", "_content_sha256", "_status", "_source_commit", "_source_path", "_source_type", "_ingested_at")

    new = fm.remove_fields(text, moved)

    assert new == (
        "---\r\n"
        "_artmind_id: id-1\r\n"
        "_domain: general\r\n"
        "title: Mine\r\n"
        "tags:\r\n"
        "- a\r\n"
        "---\r\n"
        "\r\nBody\r\n"
    )


def test_removing_keys_that_are_not_there_changes_nothing():
    text = USER_BLOCK + BODY

    assert fm.remove_fields(text, ("_version", "_ingested_at")) == text
    assert fm.remove_fields("no frontmatter\n", ("_version",)) == "no frontmatter\n"


def test_a_key_the_line_editor_cannot_see_is_refused_not_half_removed():
    text = '---\n"_version": 2\n_artmind_id: id\n---\nBody\n'

    with pytest.raises(fm.FrontmatterEditError):
        fm.remove_fields(text, ("_version",))


def test_unparsable_frontmatter_is_refused():
    with pytest.raises(fm.FrontmatterEditError):
        fm.set_fields("---\n: : bad: [\n---\nBody\n", {"_artmind_id": "id"})


def test_write_note_is_atomic_keeps_the_mode_and_leaves_no_scratch(tmp_path):
    note = tmp_path / "notes" / "a.md"
    note.parent.mkdir()
    note.write_bytes(b"old\r\n")
    os.chmod(note, 0o600)
    (tmp_path / ".artmind").mkdir()               # a vault: `.artmind/data` is made under it
    scratch = tmp_path / ".artmind" / "data"

    fm.write_note(note, "new\r\n", scratch_dir=scratch)

    assert note.read_bytes() == b"new\r\n"
    assert stat.S_IMODE(note.stat().st_mode) == 0o600
    assert list(scratch.iterdir()) == []
    assert sorted(p.name for p in note.parent.iterdir()) == ["a.md"]


def test_read_note_keeps_crlf(tmp_path):
    note = tmp_path / "a.md"
    note.write_bytes(b"---\r\n_domain: d\r\n---\r\nBody\r\n")

    assert fm.read_note(note) == "---\r\n_domain: d\r\n---\r\nBody\r\n"
