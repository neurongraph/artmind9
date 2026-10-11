"""Findings by schema element across dossiers; attaching a trace to a dossier."""

import json
import time
from datetime import date

import pytest

from artmind.reviews import aggregate, scaffold
from artmind.reviews import dossier as dz
from artmind.traces import store


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    import artmind.db as db
    import paths

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    monkeypatch.setattr(paths, "REVIEWS_DIR", tmp_path / "reviews")
    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "logs")


AUDIT = ("| element | check | source sentence | why the question needs it |\n|---|---|---|---|\n"
         "| `j/PERSON-WENT_TO->` | coverage | I went to Lakeport with Mira. | count |\n"
         "| j/PERSON-WENT_TO-> | Reification | same | trip |\n"
         "| j/PERSON | identity | I went | narrator |\n")


def _dossier(rid, *, status="open", elements=(), audit="", traces=("t1",)):
    sections = {t: "" for t in dz.SECTIONS}
    sections["Shape audit"] = audit
    d = dz.Dossier(meta={"review_id": rid, "status": status, "stage": "extraction", "elements": list(elements),
                         "trace_ids": list(traces), "private_terms": []}, sections=sections)
    dz.save(d)
    return d


def test_findings_from_table_and_frontmatter():
    d = _dossier("rv-2031-01-01-aaaaaa", elements=["j/PERSON-WENT_TO->", "j/LOCATION"], audit=AUDIT)
    assert aggregate.findings(d) == [
        {"element": "j/PERSON-WENT_TO->", "check": "coverage"},
        {"element": "j/PERSON-WENT_TO->", "check": "reification"},
        {"element": "j/PERSON", "check": "identity"},
        {"element": "j/LOCATION", "check": None},
    ]


def test_shape_report_groups_and_ranks():
    _dossier("rv-2031-01-01-aaaaaa", elements=["j/PERSON-WENT_TO->"], audit=AUDIT, traces=("t1", "t2"))
    _dossier("rv-2031-01-02-bbbbbb", elements=["j/PERSON-WENT_TO->"], traces=("t3",))
    _dossier("rv-2031-01-03-cccccc", elements=["k/THING"], traces=("t4",))
    _dossier("rv-2031-01-04-dddddd", status="verified", elements=["j/PERSON-WENT_TO->"], traces=("t5",))
    rows = aggregate.shape_report()
    top = rows[0]
    assert top["element"] == "j/PERSON-WENT_TO->"
    assert top["traces"] == 3                       # t1, t2, t3 -- verified dossier excluded
    assert [d["review_id"] for d in top["dossiers"]] == ["rv-2031-01-01-aaaaaa", "rv-2031-01-02-bbbbbb"]
    assert top["checks"] == {"coverage": 1, "reification": 1}
    assert [r["element"] for r in aggregate.shape_report(domain="k")] == ["k/THING"]
    assert any(r["traces"] == 4 for r in aggregate.shape_report(statuses=dz.STATUSES))


def _trace(tid):
    store.insert_turn(tid, source="admin", question=f"Where did Person {tid} go?", started_at=time.time())
    store.update_turn(tid, answer="Lakeport.", status="done", retrieval_path="[]", domains='["j"]')
    store.insert_span({"span_id": f"s-{tid}", "trace_id": tid, "command": "query entity-resolve",
                       "args": json.dumps({"reference": f"Person {tid}"}), "exit_code": 0, "duration_ms": 1,
                       "rows": 1, "rows_total": None, "truncated": 0,
                       "evidence": json.dumps({"resolved": [{"id": "e", "name": f"Person {tid}", "score": 1}]}),
                       "started_at": time.time()}, [])


def test_attach_adds_symptom_trace_terms_and_log():
    _trace("t9")
    d = _dossier("rv-2031-01-01-aaaaaa", traces=("t1",))
    d.sections["Symptom"] = "first symptom"
    scaffold.attach(d, "t9", today=date(2031, 1, 5))
    assert d.meta["trace_ids"] == ["t1", "t9"]
    assert "Person t9" in d.meta["private_terms"]
    assert d.sections["Symptom"].startswith("first symptom\n\n### Also seen in trace `t9`")
    assert "> Where did Person t9 go?" in d.sections["Symptom"]
    assert d.sections["Log"].endswith("- 2031-01-05 attached trace `t9`")
    reparsed = dz.parse(dz.render(d))           # the ### heading stays inside Symptom
    assert reparsed.sections["Symptom"] == d.sections["Symptom"]


def test_attach_refusals():
    _trace("t9")
    d = _dossier("rv-2031-01-01-aaaaaa", traces=("t9",))
    with pytest.raises(dz.DossierError, match="already"):
        scaffold.attach(d, "t9")
    closed = _dossier("rv-2031-01-02-bbbbbb", status="verified", traces=("t1",))
    with pytest.raises(dz.DossierError, match="reopen"):
        scaffold.attach(closed, "t9")
    with pytest.raises(dz.DossierError, match="no trace"):
        scaffold.attach(_dossier("rv-2031-01-03-cccccc"), "missing")
