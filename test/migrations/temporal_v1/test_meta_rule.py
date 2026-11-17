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


def test_failed_edit_restores_original_bytes(meta, monkeypatch):
    meta.write_text(HEAD + TAIL)
    before = meta.read_bytes()
    monkeypatch.setattr(meta_rule, "_edited", lambda text: "a: [unclosed\n")
    with pytest.raises(meta_rule.MetaRuleError):
        meta_rule.apply(_ctx())
    assert meta.read_bytes() == before
    assert not list(meta.parent.glob(".meta.*"))


def test_prompt_builder_no_longer_raises_after_the_step(meta):
    meta.write_text(HEAD + TAIL)
    decl = {"identity": "occasion", "description": "d"}
    with pytest.raises(prompt_builder.MetaIncomplete):
        prompt_builder._render_class_block("Trip", decl, yaml.safe_load(meta.read_text()))
    meta_rule.apply(_ctx())
    out = prompt_builder._render_class_block("Trip", decl, yaml.safe_load(meta.read_text()))
    assert "NEVER add a date" in out
