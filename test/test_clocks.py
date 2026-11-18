"""Clocks (spec 2026-11-15 §6.1, question 18): record time UTC, document time local."""
import datetime as dt

from artmind import temporal, update


def test_update_records_in_utc_and_dates_documents_locally(monkeypatch):
    class FrozenDateTime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 1, 1, 23, 30, tzinfo=dt.timezone.utc) if tz else cls(2026, 1, 2, 7, 30)

    class FrozenDate(dt.date):
        @classmethod
        def today(cls):
            return dt.date(2026, 1, 2)

    monkeypatch.setattr(update, "datetime", FrozenDateTime)
    monkeypatch.setattr(update, "date", FrozenDate)
    created_at, today = update._clocks()
    assert created_at.endswith("+00:00") and created_at.startswith("2026-01-01T23:30")
    assert today == "2026-01-02"


def test_the_ingestion_default_is_the_local_date(monkeypatch):
    class Frozen(dt.date):
        @classmethod
        def today(cls):
            return dt.date(2026, 1, 2)       # local; UTC may still be 2026-01-01

    monkeypatch.setattr(temporal, "date", Frozen)
    out = temporal.lift_document_dates("", {}, {}, {"valid_from": "ingestion_date"})
    assert out["valid_from"] == "2026-01-02"


def test_an_unparsed_value_still_gets_the_ingestion_default(monkeypatch):
    class Frozen(dt.date):
        @classmethod
        def today(cls):
            return dt.date(2026, 1, 2)

    monkeypatch.setattr(temporal, "date", Frozen)
    out = temporal.lift_document_dates(
        "Body\n", {"effective_date": "next review"}, {"valid_from": ["Effective Date"]},
        defaults={"valid_from": "ingestion_date"},
    )
    assert out["valid_from"] == "2026-01-02"
    assert out["unparsed"] == ["valid_from"]
