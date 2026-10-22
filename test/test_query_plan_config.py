"""Per-domain plan config (spec 2026-10-22 §4.5)."""

import re

import pytest

from artmind.query_plan import config


@pytest.fixture(autouse=True)
def _plan_dir(tmp_path, monkeypatch):
    import paths

    monkeypatch.setattr(paths, "DOMAIN_PLAN_DIR", tmp_path)
    return tmp_path


def _write(tmp_path, domain, text):
    (tmp_path / f"{domain}.yaml").write_text(text)


def test_no_file_means_defaults():
    cfg = config.load_config("journal")
    assert cfg.narrator is None
    assert cfg.confidence_threshold == 0.7
    assert cfg.first_person == ("i", "me", "my", "mine", "myself")
    assert cfg.classifier == "auto"


def test_a_full_file_is_read(tmp_path):
    _write(tmp_path, "journal", "narrator: {entity_class: person, name: Diarist}\n"
                                "confidence_threshold: 0.8\nclassifier: lexical\ndecision_model: tev1\n")
    cfg = config.load_config("journal")
    assert cfg.narrator == config.Narrator("PERSON", "Diarist")
    assert (cfg.confidence_threshold, cfg.classifier, cfg.decision_model) == (0.8, "lexical", "tev1")


def test_a_parent_file_applies_to_children_unless_they_have_their_own(tmp_path):
    _write(tmp_path, "bank", "confidence_threshold: 0.9\n")
    _write(tmp_path, "bank.cases", "confidence_threshold: 0.6\n")
    assert config.load_config("bank.policy").confidence_threshold == 0.9
    assert config.load_config("bank.cases").confidence_threshold == 0.6


@pytest.mark.parametrize("text, message", [
    ("colour: blue\n", "unknown key"),
    ("confidence_threshold: 1.5\n", "confidence_threshold"),
    ("confidence_threshold: true\n", "confidence_threshold"),
    ("classifier: llm\n", "classifier"),
    ("narrator: Diarist\n", "narrator"),
    ("first_person: []\n", "first_person"),
    ("[not, a, mapping]\n", "mapping"),
    ("narrator: {entity_class: PERSON\n", "not valid YAML"),
])
def test_invalid_files_name_the_problem(tmp_path, text, message):
    _write(tmp_path, "journal", text)
    with pytest.raises(config.PlanConfigError, match=message):
        config.load_config("journal")


@pytest.mark.parametrize("raw, expected", [
    ("person", "PERSON"),
    ("Person Of Interest", "PERSON_OF_INTEREST"),
])
def test_the_narrator_class_is_normalised_like_ingestion(tmp_path, raw, expected):
    _write(tmp_path, "journal", f"narrator: {{entity_class: '{raw}', name: Diarist}}\n")
    assert config.load_config("journal").narrator == config.Narrator(expected, "Diarist")


@pytest.mark.parametrize("raw", ["!!", "123 x"])
def test_a_narrator_class_with_no_usable_name_is_refused(tmp_path, raw):
    _write(tmp_path, "journal", f"narrator: {{entity_class: '{raw}', name: Diarist}}\n")
    with pytest.raises(config.PlanConfigError, match="entity_class"):
        config.load_config("journal")


def test_an_unreadable_file_is_a_plan_config_error(tmp_path):
    (tmp_path / "journal.yaml").write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(config.PlanConfigError, match="cannot be read"):
        config.load_config("journal")


def test_an_invalid_parent_file_is_reported_for_its_children(tmp_path):
    parent = tmp_path / "bank.yaml"
    parent.write_text("colour: blue\n")
    with pytest.raises(config.PlanConfigError, match=re.escape(str(parent))):
        config.load_config("bank.policy")


def test_merge_takes_the_most_cautious_threshold():
    a = config.PlanConfig(confidence_threshold=0.6, narrator=config.Narrator("PERSON", "Diarist"))
    b = config.PlanConfig(confidence_threshold=0.9, classifier="lexical")
    merged = config.merge([a, b])
    assert merged.confidence_threshold == 0.9
    assert merged.narrator == config.Narrator("PERSON", "Diarist")
    assert merged.classifier == "auto"  # they disagree


def test_an_odd_domain_name_has_no_file():
    assert config.config_file("../etc/passwd") is None


def test_a_traversal_name_cannot_reach_a_file_outside_the_plan_dir(tmp_path):
    outside = tmp_path.parent / "evil.yaml"
    outside.write_text("confidence_threshold: 0.1\n")
    try:
        assert config.config_file("../evil") is None
        assert config.load_config("../evil") == config.PlanConfig()
    finally:
        outside.unlink()


def test_a_trailing_newline_is_not_part_of_the_name(tmp_path):
    _write(tmp_path, "a", "confidence_threshold: 0.8\n")
    assert config._DOMAIN_RE.fullmatch("a\n") is None
    assert config.config_file("a\n") is None


def test_real_domain_names_find_their_files(tmp_path):
    _write(tmp_path, "bank.cases", "confidence_threshold: 0.6\n")
    _write(tmp_path, "my-domain", "confidence_threshold: 0.8\n")
    assert config.config_file("bank.cases") == tmp_path / "bank.cases.yaml"
    assert config.config_file("my-domain") == tmp_path / "my-domain.yaml"
