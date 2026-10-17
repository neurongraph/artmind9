from _insights_helpers import ent, make_ctx, year_days
from artmind.insights.detectors import Skip
from artmind.insights.detectors.salience import SALIENCE_TREND, TYPE_TREND

OCCURRENT = {"entity_types": {"EVENT": {"kind": "occurrent"}, "PERSON": {"kind": "recurrent"}}}


def test_rising_and_fading_entities_are_found_flat_is_not():
    rising = ent("p1", "PERSON", year_days(2022, 2) + year_days(2023, 6) + year_days(2024, 20), name="Asha")
    fading = ent("p2", "PERSON", year_days(2022, 20) + year_days(2023, 6) + year_days(2024, 2), name="Ben")
    flat = ent("p3", "PERSON", year_days(2022, 8) + year_days(2023, 8) + year_days(2024, 8), name="Cleo")
    ctx = make_ctx([rising, fading, flat])

    found = {f.key: f for f in SALIENCE_TREND.run(ctx, "PERSON")}

    assert set(found) == {"salience_trend:PERSON:p1", "salience_trend:PERSON:p2"}
    up = found["salience_trend:PERSON:p1"]
    assert up.stats["direction"] == "rising" and up.title == "Rising: Asha"
    assert up.stats["rates"] == {"2022": 2.0, "2023": 6.0, "2024": 20.0}
    assert up.card == {"type": "series", "periods": ["2022", "2023", "2024"], "values": [2.0, 6.0, 20.0], "unit": "per 100 documents"}
    assert up.subjects == [{"_id": "p1", "name": "Asha", "class": "PERSON"}]
    assert 0 < up.score <= 1
    assert found["salience_trend:PERSON:p2"].stats["direction"] == "fading"


def test_rates_are_normalised_by_documents_per_period():
    e = ent("p1", "PERSON", year_days(2022, 10) + year_days(2023, 10))
    ctx = make_ctx([e], docs_per_period={"2022": 200, "2023": 50})
    (f,) = SALIENCE_TREND.run(ctx, "PERSON")
    assert f.stats["rates"] == {"2022": 5.0, "2023": 20.0}


def test_low_support_and_anchors_are_ignored():
    thin = ent("p1", "PERSON", year_days(2022, 1) + year_days(2023, 1) + year_days(2024, 5))
    me = ent("me", "PERSON", year_days(2022, 2) + year_days(2023, 6) + year_days(2024, 20))
    ctx = make_ctx([thin, me], anchors={"me"})
    assert SALIENCE_TREND.run(ctx, "PERSON") == []


def test_plan_skips_without_dates_or_periods():
    e = ent("p1", "PERSON", year_days(2022, 5))
    assert isinstance(SALIENCE_TREND.plan(make_ctx([e], default_time_share=0.9)), Skip)
    assert isinstance(SALIENCE_TREND.plan(make_ctx([e], docs_per_period={"2022": 100})), Skip)
    assert SALIENCE_TREND.plan(make_ctx([e])) == ["PERSON"]


def test_type_trend_aggregates_occurrent_entities_by_type():
    events = [ent(f"w{i}", "EVENT", [d], type="Work") for i, d in enumerate(year_days(2022, 2) + year_days(2023, 5) + year_days(2024, 15))]
    events += [ent(f"s{i}", "EVENT", [d], type="sport") for i, d in enumerate(year_days(2022, 6) + year_days(2023, 6) + year_days(2024, 6))]
    ctx = make_ctx(events, schema=OCCURRENT)

    assert TYPE_TREND.plan(ctx) == ["EVENT"]
    (f,) = TYPE_TREND.run(ctx, "EVENT")
    assert f.key == "type_trend:EVENT:work" and f.stats["type"] == "work" and f.stats["direction"] == "rising"
    assert f.subjects == []
