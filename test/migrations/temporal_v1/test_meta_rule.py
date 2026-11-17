"""Step `meta_rule` (plan Task 34): the occasion naming rule reaches a vault's meta.yaml."""
import pytest
import yaml

from artmind import prompt_builder
from artmind.migrations.temporal_v1 import meta_rule
from artmind.migrations.temporal_v1 import steps as steps_mod
from artmind.migrations.temporal_v1.steps import MigrationContext

HEAD = "# A vault's own notes -- keep me\nkind_naming_rules:\n  recurrent: >-\n    name the thing.\n\n"
TAIL = "default_persona: \"an analyst\"\n"


@pytest.fixture
def meta(tmp_path, monkeypatch):
    path = tmp_path / "meta.yaml"
    monkeypatch.setattr(meta_rule, "DOMAIN_META_PATH", path)
    return path


def _ctx():
    return MigrationContext(domains=None, session_factory=None)


def _rule(path):
    return yaml.safe_load(path.read_text())["identity_naming_rules"]["occasion"]


def test_registered_and_travels():
    step = next(s for s in steps_mod.STEPS if s.name == "meta_rule")
    assert step.travels is True


def test_appends_block_and_keeps_comments_byte_for_byte(meta):
    meta.write_text(HEAD + TAIL)
    before = meta.read_bytes()
    assert meta_rule.pending(_ctx()) == {"meta": 1}
    assert meta_rule.writes(_ctx()) == [meta]
    meta_rule.apply(_ctx())
    after = meta.read_bytes()
    assert after.startswith(before.rstrip(b"\n") + b"\n\nidentity_naming_rules:\n")
    assert "NEVER add a date" in _rule(meta)
    assert meta_rule.pending(_ctx()) == {}


def test_empty_flow_mapping_gains_occasion(meta):
    meta.write_text(HEAD + "identity_naming_rules: {}  # later\n" + TAIL)
    meta_rule.apply(_ctx())
    text = meta.read_text()
    assert "# A vault's own notes -- keep me" in text and "# later" in text
    assert "NEVER add a date" in _rule(meta)
    assert yaml.safe_load(text)["default_persona"] == "an analyst"


def test_existing_block_mapping_keeps_its_entries_and_indent(meta):
    meta.write_text(HEAD + "identity_naming_rules:\n    other: >-\n      keep this.\n" + TAIL)
    meta_rule.apply(_ctx())
    data = yaml.safe_load(meta.read_text())["identity_naming_rules"]
    assert data["other"] == "keep this." and "NEVER add a date" in data["occasion"]


def test_nonempty_inline_mapping_fails_cleanly(meta):
    meta.write_text(HEAD + "identity_naming_rules: {other: x}\n" + TAIL)
    before = meta.read_bytes()
    with pytest.raises(meta_rule.MetaRuleError, match="inline"):
        meta_rule.apply(_ctx())
    assert meta.read_bytes() == before


def test_second_run_is_a_noop(meta):
    meta.write_text(HEAD + TAIL)
    meta_rule.apply(_ctx())
    once = meta.read_bytes()
    assert meta_rule.apply(_ctx()) == {}
    assert meta.read_bytes() == once


def test_dry_run_writes_nothing(meta):
    meta.write_text(HEAD + TAIL)
    before = meta.read_bytes()
    assert meta_rule.apply(_ctx(), dry_run=True) == {}
    assert meta.read_bytes() == before


def test_no_meta_file_is_nothing_to_do(meta):
    assert meta_rule.pending(_ctx()) == {}
    assert meta_rule.writes(_ctx()) == []
    assert meta_rule.apply(_ctx()) == {}
    assert not meta.exists()


def _refuses(meta, text, match, raw=None):
    meta.write_bytes(raw if raw is not None else text.encode())
    before = meta.read_bytes()
    with pytest.raises(meta_rule.MetaRuleError, match=match):
        meta_rule.apply(_ctx())
    assert meta.read_bytes() == before
    assert not list(meta.parent.glob(".meta.*"))


def test_quoted_key_is_refused_not_duplicated(meta):
    _refuses(meta, HEAD + '"identity_naming_rules":\n  other: x\n' + TAIL, "quoted")


def test_crlf_and_cr_endings_are_kept(meta):
    for eol in ("\r\n", "\r"):
        text = (HEAD + "identity_naming_rules: ~ # keep\n" + TAIL).replace("\n", eol)
        meta.write_bytes(text.encode())
        meta_rule.apply(_ctx())
        out = meta.read_bytes().decode()
        assert "# keep" in out and "NEVER add a date" in _rule(meta)
        assert out.replace("\r\n", "").replace("\r", "").count("\n") == 0 if eol == "\r" else "\n" not in out.replace("\r\n", "")


def test_mixed_endings_keep_every_other_rule(meta):
    meta.write_bytes(b"kind_naming_rules:\r\n  recurrent: x\nidentity_naming_rules:\n  other: keep\r\n")
    meta_rule.apply(_ctx())
    data = yaml.safe_load(meta.read_text())
    assert data["identity_naming_rules"]["other"] == "keep" and data["kind_naming_rules"]["recurrent"] == "x"
    assert "NEVER add a date" in data["identity_naming_rules"]["occasion"]


def test_empty_occasion_is_refused_by_hand(meta):
    _refuses(meta, HEAD + "identity_naming_rules:\n  occasion:\n" + TAIL, "empty; fill it by hand")


def test_tabs_unparseable_and_non_utf8_are_named(meta):
    _refuses(meta, HEAD + "identity_naming_rules:\n\tother: x\n" + TAIL, "tab|does not parse")
    _refuses(meta, "a: [unclosed\n", "does not parse")
    _refuses(meta, "", "not UTF-8", raw=b"a: \xff\xfe\n")
    assert meta_rule.writes(_ctx()) == [meta]


def test_file_mode_is_preserved(meta):
    meta.write_text(HEAD + TAIL)
    meta.chmod(0o640)
    meta_rule.apply(_ctx())
    assert meta.stat().st_mode & 0o777 == 0o640


def test_symlink_stays_a_symlink(meta, tmp_path, monkeypatch):
    real = tmp_path / "real.yaml"
    real.write_text(HEAD + TAIL)
    link = tmp_path / "link.yaml"
    link.symlink_to(real)
    monkeypatch.setattr(meta_rule, "DOMAIN_META_PATH", link)
    meta_rule.apply(_ctx())
    assert link.is_symlink()
    assert "NEVER add a date" in yaml.safe_load(real.read_text())["identity_naming_rules"]["occasion"]


def test_prompt_builder_no_longer_raises_after_the_step(meta):
    meta.write_text(HEAD + TAIL)
    decl = {"identity": "occasion", "description": "d"}
    with pytest.raises(prompt_builder.MetaIncomplete):
        prompt_builder._render_class_block("Trip", decl, yaml.safe_load(meta.read_text()))
    meta_rule.apply(_ctx())
    out = prompt_builder._render_class_block("Trip", decl, yaml.safe_load(meta.read_text()))
    assert "NEVER add a date" in out
