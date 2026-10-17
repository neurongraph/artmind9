import pytest

import paths
from artmind.insights import config as cfg_mod
from artmind.insights.config import KNOWN_DETECTORS, load_config, parse_config
from artmind.insights.model import InsightsError


@pytest.fixture
def cfg_root(tmp_path, monkeypatch):
    root = tmp_path / "insights-config"
    monkeypatch.setattr(paths, "DOMAIN_INSIGHTS_DIR", root)
    return root


def test_missing_file_gives_defaults(cfg_root):
    c = load_config("demo")
    assert (c.domain, c.period, c.min_evidence_days, c.min_period_docs) == ("demo", "year", 4, 20)
    assert (c.max_findings, c.keep_runs, c.narrate, c.anchors) == (25, 12, True, ())
    assert cfg_mod.config_hash("demo") == ""


def test_file_values_are_read_and_hashed(cfg_root):
    cfg_root.mkdir(parents=True)
    (cfg_root / "demo.yaml").write_text(
        "period: quarter\nanchors: ['@e1']\ndetectors:\n  dormancy: {silence_multiple: 4}\n  brokers: {enabled: false}\n"
    )
    c = load_config("demo")
    assert c.period == "quarter" and c.anchors == ("@e1",)
    assert c.detector("dormancy") == {"silence_multiple": 4}
    assert c.detector("salience_trend") == {}
    assert c.enabled("dormancy") and not c.enabled("brokers")
    assert len(cfg_mod.config_hash("demo")) == 64


@pytest.mark.parametrize(
    "data, message",
    [
        ({"colour": "red"}, "unknown key"),
        ({"domain": "other"}, "domain"),
        ({"period": "week"}, "period"),
        ({"min_evidence_days": 0}, "min_evidence_days"),
        ({"keep_runs": True}, "keep_runs"),
        ({"anchors": "Asha"}, "anchors"),
        ({"detectors": {"dormancy": 3}}, "detectors"),
        ({"detectors": {"dormant": {}}}, "unknown detector"),
        ({"narrate": "yes"}, "narrate"),
    ],
)
def test_invalid_config_is_refused(data, message):
    with pytest.raises(InsightsError, match=message):
        parse_config(data, "demo")


def test_bad_yaml_is_an_insights_error(cfg_root):
    cfg_root.mkdir(parents=True)
    (cfg_root / "demo.yaml").write_text("period: [unclosed\n")
    with pytest.raises(InsightsError, match="not valid YAML"):
        load_config("demo")


def test_known_detectors_cover_the_spec():
    assert {"salience_trend", "type_trend", "dormancy", "communities", "brokers", "kind_mismatch",
            "vocabulary_sprawl", "relationship_usage", "open_conflicts", "outcome_lift"} <= KNOWN_DETECTORS


def test_detector_enabled_must_be_a_bool():
    with pytest.raises(InsightsError, match="enabled"):
        parse_config({"detectors": {"dormancy": {"enabled": "false"}}}, "demo")


def test_empty_detector_entry_means_defaults():
    c = parse_config({"detectors": {"dormancy": None}}, "demo")
    assert c.enabled("dormancy") is True
    assert c.detector("dormancy") == {}


def test_domain_name_must_be_a_safe_identifier():
    with pytest.raises(InsightsError, match="invalid domain name"):
        cfg_mod.config_path("../x")
    with pytest.raises(InsightsError, match="invalid domain name"):
        load_config("../x")


@pytest.mark.parametrize("data", [
    {"detectors": {"communities": {"classes": "PERSON"}}},
    {"detectors": {"communities": {"classes": [1]}}},
    {"detectors": {"dormancy": {"min_silence_days": "x"}}},
    {"detectors": {"dormancy": {"silence_multiple": True}}},
    {"detectors": {"salience_trend": {"window": 0}}},
    {"detectors": {"communities": {"resolution": True}}},
    {"detectors": {"communities": {"resolution": -1}}},
    {"detectors": {"relationship_usage": {"min_jaccard": "0.3"}}},
])
def test_detector_setting_types_are_validated(data):
    with pytest.raises(InsightsError, match=r"detector \w+ "):
        parse_config(data, "demo")


def test_valid_detector_settings_pass():
    parse_config({"detectors": {"communities": {"classes": ["PERSON"], "resolution": 1, "min_weight": 3},
                                "relationship_usage": {"min_jaccard": 0.4}}}, "demo")


def test_outcome_block_is_validated_and_normalised():
    c = parse_config({"outcome": {"class": "EMOTION", "positive": ["Joy", " pride"], "negative": ["Sadness"]}}, "demo")
    assert c.block("outcome") == {"class": "EMOTION", "property": "type", "positive": ["joy", "pride"], "negative": ["sadness"]}
    assert c.block("open_loops") is None


def test_open_loops_and_transitions_blocks():
    c = parse_config({
        "open_loops": {"class": "PLAN", "open": ["Firm intention"], "closed": ["done"]},
        "transitions": {"extra_verbs": ["promoted_to"], "classes": ["ORGANIZATION"]},
    }, "demo")
    assert c.block("open_loops") == {"class": "PLAN", "property": "status", "urgency_property": "urgency",
                                     "open": ["firm intention"], "closed": ["done"]}
    assert c.block("transitions") == {"extra_verbs": ["PROMOTED_TO"], "classes": ["ORGANIZATION"]}


@pytest.mark.parametrize("data, message", [
    ({"outcome": {"positive": ["joy"], "negative": ["sad"]}}, "outcome.class"),
    ({"outcome": {"class": "E", "positive": [], "negative": ["sad"]}}, "outcome.positive"),
    ({"outcome": {"class": "E", "positive": ["joy"], "negative": ["Joy"]}}, "both positive and negative"),
    ({"outcome": {"class": "E", "positive": ["joy"], "negative": ["sad"], "colour": 1}}, "unknown key"),
    ({"open_loops": {"class": "PLAN"}}, "open_loops.open"),
    ({"transitions": {"extra_verbs": "joined"}}, "transitions.extra_verbs"),
    ({"detectors": {"outcome_lift": {"min_z": 0}}}, "min_z"),
    ({"detectors": {"transitions": {"cluster_days": "x"}}}, "cluster_days"),
    ({"detectors": {"same_as_candidates": {"aliases": [["a"]]}}}, "aliases"),
])
def test_phase_two_blocks_are_refused_when_invalid(data, message):
    with pytest.raises(InsightsError, match=message):
        parse_config(data, "demo")


def test_same_as_aliases_setting_is_accepted():
    c = parse_config({"detectors": {"same_as_candidates": {"aliases": [["Nana", "Grandma"]]}}}, "demo")
    assert c.detector("same_as_candidates")["aliases"] == [["Nana", "Grandma"]]
