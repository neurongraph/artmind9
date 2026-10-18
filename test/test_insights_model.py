import pytest

from artmind.insights.model import FORMAT, Finding, InsightsError, PlanEntry, Run


def _finding(**over):
    base = dict(key="dormancy:PERSON:p1", detector="dormancy", kind="corpus", score=0.5, title="Quiet: Asha")
    return Finding(**{**base, **over})


def test_finding_defaults_and_round_trip():
    f = _finding(stats={"headline": 3})
    assert f.diff == "new" and f.subjects == [] and f.action is None and f.narration is None
    assert Finding.from_dict(f.to_dict()) == f


def test_finding_rejects_bad_score_and_kind():
    with pytest.raises(ValueError):
        _finding(score=1.2)
    with pytest.raises(ValueError):
        _finding(kind="other")


def test_finding_from_dict_ignores_unknown_keys():
    d = {**_finding().to_dict(), "future_field": 1}
    assert Finding.from_dict(d) == _finding()


def test_run_round_trip_and_format_guard():
    run = Run(
        domain="demo", created_at="2026-10-17T09:30:12Z", as_of="2026-10-01",
        fingerprint={"entities": 3}, anchors=[], profile={}, circles=[], diff={},
        plan=[PlanEntry(detector="dormancy", status="ran", target="PERSON", findings=1)],
        findings=[_finding()],
    )
    d = run.to_dict()
    assert d["format"] == FORMAT
    assert Run.from_dict(d) == run
    with pytest.raises(InsightsError):
        Run.from_dict({**d, "format": FORMAT + 1})


def test_narration_key_and_run_llm_round_trip():
    f = _finding(narration="Asha has gone quiet.", narration_key="abc")
    assert Finding.from_dict(f.to_dict()).narration_key == "abc"
    run = Run(domain="demo", created_at="t", as_of=None, fingerprint={}, anchors=[], profile={}, plan=[],
              findings=[f], circles=[], diff={}, llm={"calls": 1})
    assert Run.from_dict(run.to_dict()).llm == {"calls": 1}
    legacy = run.to_dict()
    del legacy["llm"]
    assert Run.from_dict(legacy).llm == {}
