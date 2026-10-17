from datetime import date, timedelta

from _insights_helpers import ent, make_ctx
from artmind.insights.detectors import Skip
from artmind.insights.detectors.dormancy import DORMANCY


def every(start: str, step: int, n: int) -> list[str]:
    d0 = date.fromisoformat(start)
    return [(d0 + timedelta(days=i * step)).isoformat() for i in range(n)]


def test_quiet_relative_to_own_cadence_fires():
    quiet = ent("p1", "PERSON", every("2023-01-01", 10, 20), name="Asha")   # last 2023-07-10
    steady = ent("p2", "PERSON", every("2023-01-05", 30, 24), name="Ben")   # last 2024-11-25
    ctx = make_ctx([quiet, steady], as_of="2024-12-31")

    (f,) = DORMANCY.run(ctx, "PERSON")

    assert f.key == "dormancy:PERSON:p1" and f.title == "Quiet: Asha"
    assert f.stats == {"last_seen": "2023-07-10", "cadence_days": 10.0, "silence_days": 540, "support": 20, "headline": 540}
    assert 0 < f.score <= 1


def test_thresholds_come_from_config():
    quiet = ent("p1", "PERSON", every("2024-01-01", 10, 20))  # last 2024-07-09
    ctx = make_ctx([quiet], as_of="2024-09-30", config={"detectors": {"dormancy": {"min_silence_days": 120}}})
    assert DORMANCY.run(ctx, "PERSON") == []


def test_same_day_entities_and_anchors_are_skipped():
    burst = ent("p1", "PERSON", ["2023-01-01"] * 5)
    me = ent("me", "PERSON", every("2023-01-01", 10, 20))
    ctx = make_ctx([burst, me], anchors={"me"}, as_of="2024-12-31")
    assert DORMANCY.run(ctx, "PERSON") == []


def test_plan_needs_dates():
    e = ent("p1", "PERSON", every("2023-01-01", 10, 20))
    assert isinstance(DORMANCY.plan(make_ctx([e], default_time_share=0.9)), Skip)
    assert DORMANCY.plan(make_ctx([e])) == ["PERSON"]
