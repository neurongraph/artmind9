"""The contract is recorded on every trace turn and carried by every benchmark run (P3 spec §8.1)."""

import pytest

from artmind import benchmark
from artmind.traces import store
from artmind.traces.recorder import TraceRecorder


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    monkeypatch.setenv("ARTMIND_PLAN_SHADOW", "0")


def test_a_turn_records_its_contract():
    rec = TraceRecorder.start(source="admin", question="q", contract="p2-1")
    assert store.get_turn(rec.trace_id)["contract"] == "p2-1"


def test_the_default_contract_is_classic():
    rec = TraceRecorder.start(source="admin", question="q")
    assert store.get_turn(rec.trace_id)["contract"] == "classic"


def test_a_benchmark_run_carries_its_contract(tmp_path):
    questions = [{"seq": 1, "qid": "Q1", "title": "t", "question": "invented?", "eval_comment": None}]
    run_id = benchmark._create_benchmark_run(str(tmp_path / "q.md"), questions, contract="p2-1")
    assert benchmark._get_benchmark_run(run_id)["contract"] == "p2-1"
    other = benchmark._create_benchmark_run(str(tmp_path / "q.md"), questions)
    assert benchmark._get_benchmark_run(other)["contract"] == "classic"


def test_a_bad_contract_is_refused(tmp_path):
    with pytest.raises(ValueError):
        benchmark._create_benchmark_run(str(tmp_path / "q.md"), [], contract="Not Valid")
