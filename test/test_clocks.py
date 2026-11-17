"""Clocks (spec 2026-11-15 §6.1, question 18): record time UTC, document time local."""
import datetime as dt
import inspect

from artmind import temporal, update


def test_update_records_in_utc_and_dates_documents_locally():
    source = inspect.getsource(update.write_user_chat)
    assert "datetime.now(timezone.utc).isoformat()" in source
    assert "datetime.now().isoformat()" not in source
    assert "date.today().isoformat()" in source


def test_the_ingestion_default_is_the_local_date(monkeypatch):
    class Frozen(dt.date):
        @classmethod
        def today(cls):
            return dt.date(2026, 1, 2)       # local; UTC may still be 2026-01-01

    monkeypatch.setattr(temporal, "date", Frozen)
    out = temporal.lift_document_dates("", {}, {}, {"valid_from": "ingestion_date"})
    assert out["valid_from"] == "2026-01-02"
