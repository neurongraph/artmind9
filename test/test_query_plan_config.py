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


@pytest.mark.parametrize("name", ["a", "bank.cases", "my-domain", "x_1.y-2"])
def test_is_domain_name_accepts_dotted_and_hyphenated_names(name):
    assert config.is_domain_name(name) is True


@pytest.mark.parametrize("name", ["a\n", "../x", "", "a/b", ".hidden", None])
def test_is_domain_name_rejects_newlines_paths_and_empty(name):
    assert config.is_domain_name(name) is False


def test_real_domain_names_find_their_files(tmp_path):
    _write(tmp_path, "bank.cases", "confidence_threshold: 0.6\n")
    _write(tmp_path, "my-domain", "confidence_threshold: 0.8\n")
    assert config.config_file("bank.cases") == tmp_path / "bank.cases.yaml"
    assert config.config_file("my-domain") == tmp_path / "my-domain.yaml"


# ── slot keys (planner v2 §4) ────────────────────────────────────────────────

_FULL = """\
narrator:
  entity_class: person
  name: Diarist
  aliases: ["  Dia ", "dia", "D."]
categories:
  Doctor: [Doctor, Dentist, "checkup", "dentist"]
  illness: [Illness]
kinship:
  Uncle: [Unc, "Oom"]
scope_rules:
  - {id: we_is_family, when: {mention: "We"}, set: {participants: family}}
  - {id: no_work, when: {not_cue: ["Work", "office"]}, exclude_classes: [work event]}
  - {id: friction, when: {route: relationship}, require_participant: narrator}
calendar:
  - {name: Lantern Fair, month_day: "10-20", span_days: 5}
  - {name: Leap Day, month_day: "02-29", recurring: once, year: 2024}
"""


def _err(tmp_path, text, message):
    _write(tmp_path, "d", text)
    with pytest.raises(config.PlanConfigError, match=message):
        config.load_config("d")


def test_all_new_keys_parse(tmp_path):
    _write(tmp_path, "d", _FULL)
    cfg = config.load_config("d")
    assert cfg.narrator == config.Narrator("PERSON", "Diarist", ("Dia", "D."))
    assert cfg.categories == (("doctor", ("Doctor", "Dentist", "checkup")), ("illness", ("Illness",)))
    assert cfg.kinship == (("uncle", ("unc", "oom")),)
    assert cfg.scope_rules[0] == config.ScopeRule("we_is_family", when_mention="we", set_participants="family")
    assert cfg.scope_rules[1] == config.ScopeRule("no_work", when_not_cue=("work", "office"),
                                                  exclude_classes=("WORK_EVENT",))
    assert cfg.scope_rules[2].when_route == "relationship" and cfg.scope_rules[2].require_participant == "narrator"
    assert cfg.calendar == (config.CalendarEvent("Lantern Fair", "10-20", 5, "yearly", None),
                            config.CalendarEvent("Leap Day", "02-29", 1, "once", 2024))
    hash(cfg)


def test_defaults_are_empty():
    cfg = config.PlanConfig()
    assert cfg.categories == cfg.kinship == cfg.scope_rules == cfg.calendar == ()
    assert config.Narrator("PERSON", "X").aliases == ()


def test_category_and_kin_lookup_handle_case_and_plurals(tmp_path):
    _write(tmp_path, "d", "categories:\n  doctor: [Doctor]\n  Trips: [Trip]\n  Illness: [Illness]\n"
                          "kinship:\n  uncle: [unc]\n  aunts: [aunt]\n")
    cfg = config.load_config("d")
    assert cfg.category("Doctors") == ("Doctor",)
    assert cfg.category("DOCTOR") == ("Doctor",)
    assert cfg.category("trip") == ("Trip",)
    assert cfg.category("illnesses") == ("Illness",)
    assert cfg.category("nurse") is None
    assert cfg.kin("Uncles") == ("unc",)
    assert cfg.kin("aunt") == ("aunt",)
    assert cfg.kin("cousin") is None


@pytest.mark.parametrize("text,message", [
    ("narrator: {entity_class: P, name: A, aliases: Dia}\n", "narrator aliases"),
    ("narrator: {entity_class: P, name: A, aliases: ['']}\n", "narrator aliases"),
    ("narrator: {entity_class: P, name: A, extra: 1}\n", "narrator must be"),
    ("categories: [a]\n", "categories must be a mapping"),
    ("categories: {doctor: []}\n", "non-empty list"),
    ("categories: {doctor: [a, 1]}\n", "non-empty list"),
    ("categories: {doctor: [a], Doctor: [b]}\n", "twice"),
    ("kinship: {uncle: oom}\n", "non-empty list"),
    ("scope_rules: {}\n", "scope_rules must be a list"),
    ("scope_rules: [x]\n", "must be a mapping"),
    ("scope_rules: [{id: Bad-Id, when: {mention: we}, require_participant: narrator}]\n", "snake_case"),
    ("scope_rules: [{when: {mention: we}, require_participant: narrator}]\n", "snake_case"),
    ("scope_rules: [{id: a, when: {mention: we}, require_participant: narrator}, "
     "{id: a, when: {mention: us}, require_participant: narrator}]\n", "used twice"),
    ("scope_rules: [{id: a, require_participant: narrator}]\n", "needs when"),
    ("scope_rules: [{id: a, when: {}, require_participant: narrator}]\n", "needs when"),
    ("scope_rules: [{id: a, when: {colour: red}, require_participant: narrator}]\n", "needs when"),
    ("scope_rules: [{id: a, when: {mention: we}}]\n", "needs an action"),
    ("scope_rules: [{id: a, when: {mention: we}, hide: x}]\n", "allowed: id, when, set, exclude_classes, require_participant"),
    ("scope_rules: [{id: a, when: {mention: we}, set: {colour: x}}]\n", "set must be"),
    ("scope_rules: [{id: a, when: {mention: we}, set: {participants: ''}}]\n", "set must be"),
    ("scope_rules: [{id: a, when: {mention: ''}, require_participant: x}]\n", "when.mention"),
    ("scope_rules: [{id: a, when: {not_cue: []}, require_participant: x}]\n", "when.not_cue"),
    ("scope_rules: [{id: a, when: {route: 3}, require_participant: x}]\n", "when.route"),
    ("scope_rules: [{id: a, when: {mention: we}, exclude_classes: ['!!']}]\n", "usable class name"),
    ("scope_rules: [{id: a, when: {mention: we}, require_participant: ''}]\n", "require_participant"),
    ("calendar: {}\n", "calendar must be a list"),
    ("calendar: [{name: Fair}]\n", "name and month_day"),
    ("calendar: [{name: Fair, month_day: '10-20', colour: x}]\n", "name and month_day"),
    ("calendar: [{name: '', month_day: '10-20'}]\n", "name must be"),
    ("calendar: [{name: Fair, month_day: '13-01'}]\n", "real date"),
    ("calendar: [{name: Fair, month_day: '02-30'}]\n", "real date"),
    ("calendar: [{name: Fair, month_day: '1-5'}]\n", "real date"),
    ("calendar: [{name: Fair, month_day: '10-20', span_days: 0}]\n", "span_days"),
    ("calendar: [{name: Fair, month_day: '10-20', span_days: 61}]\n", "span_days"),
    ("calendar: [{name: Fair, month_day: '10-20', span_days: true}]\n", "span_days"),
    ("calendar: [{name: Fair, month_day: '10-20', recurring: monthly}]\n", "recurring"),
    ("calendar: [{name: Fair, month_day: '10-20', recurring: once}]\n", "year is required"),
    ("calendar: [{name: Fair, month_day: '10-20', year: 2024}]\n", "only allowed when"),
])
def test_invalid_slot_keys_name_the_problem(tmp_path, text, message):
    _err(tmp_path, text, message)


def test_calendar_span_bounds_are_accepted(tmp_path):
    _write(tmp_path, "d", "calendar:\n  - {name: A, month_day: '01-01', span_days: 1}\n"
                          "  - {name: B, month_day: '12-31', span_days: 60}\n")
    assert [e.span_days for e in config.load_config("d").calendar] == [1, 60]


def test_merge_unions_maps_and_concatenates_rules_and_calendar():
    a = config.PlanConfig(
        narrator=config.Narrator("PERSON", "A", ("x",)),
        categories=(("doctor", ("Doctor", "Dentist")),), kinship=(("uncle", ("unc",)),),
        scope_rules=(config.ScopeRule("r1", when_mention="we"),),
        calendar=(config.CalendarEvent("Fair", "10-20"),))
    b = config.PlanConfig(
        categories=(("doctor", ("dentist", "Nurse")), ("trip", ("Trip",))), kinship=(("uncle", ("unc", "oom")),),
        scope_rules=(config.ScopeRule("r1", when_mention="us"), config.ScopeRule("r2", when_mention="they")),
        calendar=(config.CalendarEvent("Feast", "01-02"),))
    m = config.merge([a, b])
    assert m.narrator.aliases == ("x",)
    assert m.categories == (("doctor", ("Doctor", "Dentist", "Nurse")), ("trip", ("Trip",)))
    assert m.kinship == (("uncle", ("unc", "oom")),)
    assert [r.id for r in m.scope_rules] == ["r1", "r2"] and m.scope_rules[0].when_mention == "we"
    assert [e.name for e in m.calendar] == ["Fair", "Feast"]
    hash(m)


def test_plan_config_check_reports_counts_only(tmp_path):
    import json

    from click.testing import CliRunner

    from artmind.cli import cli

    body = _FULL.split("categories:", 1)[1]
    _write(tmp_path, "journal", "categories:" + body)
    _write(tmp_path, "bad", "calendar: [{name: Fair, month_day: '99-99'}]\n")
    result = CliRunner().invoke(cli, ["plan", "config", "check", "--compact"])
    by = {r["domain"]: r for r in json.loads(result.output)["results"]}
    assert result.exit_code == 1
    assert by["journal"]["ok"] is True
    assert by["journal"]["counts"] == {"categories": 2, "kinship": 1, "scope_rules": 3, "calendar": 2,
                                       "narrator_aliases": 0}
    for term in ("Dentist", "Lantern", "oom", "family"):
        assert term not in result.output
    assert by["bad"]["ok"] is False and "month_day" in by["bad"]["errors"][0]
