"""Per-stage completeness rules for a review dossier."""

import pytest

from artmind.reviews import dossier as dz
from artmind.reviews.validate import validate

REGRESSION = ("### R-rv-2026-01-02-abcdef — trips\n\n**Question:** How many trips did Person A take?\n\n"
              "**Evaluation comment:** Two trips; answered from the graph.")


def _complete(stage="schema", **meta):
    base = {"review_id": "rv-2026-01-02-abcdef", "status": "open", "stage": stage,
            "elements": ["d/Person-WENT_TO->"], "trace_ids": ["t1"], "private_terms": ["Person A"],
            "graph_state": {}, "created": "2026-01-02", "updated": "2026-01-02"}
    base.update(meta)
    sections = {t: f"filled {t}" for t in dz.SECTIONS}
    sections["Regression question"] = REGRESSION
    sections["Agent prompt"] = dz.PRIVACY_PREAMBLE + "\n\nDo the thing."
    return dz.Dossier(meta=base, sections=sections)


def test_complete_dossier_is_ok():
    result = validate(_complete())
    assert result == {"review_id": "rv-2026-01-02-abcdef", "ok": True, "errors": [], "warnings": []}


@pytest.mark.parametrize("meta,fragment", [
    ({"status": "done"}, "status"),
    ({"stage": None}, "stage"),
    ({"trace_ids": []}, "trace_ids"),
    ({"elements": ["not an element"]}, "element"),
    ({"elements": []}, "schema element"),
])
def test_frontmatter_errors(meta, fragment):
    result = validate(_complete(**meta))
    assert not result["ok"]
    assert any(fragment in e for e in result["errors"])


def test_placeholder_only_section_is_empty():
    d = _complete()
    d.sections["Diagnosis"] = "<!-- fill me -->"
    assert any("Diagnosis" in e for e in validate(d)["errors"])


def test_shape_audit_required_only_for_graph_shape_stages():
    d = _complete(stage="routing", elements=[])
    d.sections["Shape audit"] = ""
    assert validate(d)["ok"]
    d = _complete(stage="extraction")
    d.sections["Shape audit"] = ""
    assert any("Shape audit" in e for e in validate(d)["errors"])


def test_corpus_gap_needs_no_synthetic_example():
    d = _complete(stage="corpus-gap", elements=[])
    d.sections["Synthetic example"] = ""
    d.sections["Shape audit"] = ""
    d.sections["Proof"] = ""
    assert validate(d)["ok"]


def test_missing_section_and_missing_preamble():
    d = _complete()
    del d.sections["Proof"]
    d.sections["Agent prompt"] = "Do the thing."
    errors = validate(d)["errors"]
    assert any("Proof" in e for e in errors)
    assert any("privacy preamble" in e for e in errors)


def test_regression_question_must_be_one_block_with_comment():
    d = _complete()
    d.sections["Regression question"] = ("### R-x — t\n\n**Question:** Q?\n\n**Evaluation comment:** <!-- todo -->")
    assert any("Regression question" in e for e in validate(d)["errors"])


def test_warnings():
    d = _complete(private_terms=[])
    d.sections["Proof"] = "<!-- later -->"
    result = validate(d)
    assert result["ok"]
    assert any("private_terms" in w for w in result["warnings"])
    assert any("Proof" in w for w in result["warnings"])


def test_non_string_sections_do_not_raise():
    d = _complete()
    d.sections["Symptom"] = 42
    d.sections["Regression question"] = ["not", "text"]
    result = validate(d)
    assert any("Regression question" in e for e in result["errors"])
