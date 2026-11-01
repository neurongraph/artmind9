import pytest
import yaml

from artmind.retrieval_eval import labels as rl


def _doc(*entries):
    return {"labels": list(entries)}


NEEDLE = {"id": "r001", "question": "Which method?", "domains": ["papers"],
          "kind": "needle", "relevant": ["d1_001"], "source": "synthetic"}
SURVEY = {"id": "r002", "question": "What do they say about X?", "domains": ["papers"],
          "kind": "survey", "relevant_docs": ["d1", "d2"], "source": "hand"}


def test_parses_needle_and_survey():
    out = rl.parse_labels(_doc(NEEDLE, SURVEY))
    assert [l.id for l in out] == ["r001", "r002"]
    assert out[0].relevant == ("d1_001",) and out[0].kind == "needle"
    assert out[1].relevant_docs == ("d1", "d2") and out[1].source == "hand"


def test_reports_every_problem_at_once():
    bad = [
        {"id": "", "question": "q", "domains": ["d"], "kind": "needle", "relevant": ["x"]},
        {"id": "r1", "question": "", "domains": [], "kind": "other"},
        {"id": "r2", "question": "q", "domains": ["d"], "kind": "needle", "relevant": []},
        {"id": "r3", "question": "q", "domains": ["d"], "kind": "survey"},
        {"id": "r2", "question": "q", "domains": ["d"], "kind": "needle", "relevant": ["x"], "source": "bot"},
    ]
    with pytest.raises(rl.LabelError) as exc:
        rl.parse_labels(_doc(*bad))
    text = " | ".join(exc.value.errors)
    for needle in ("labels[0]: id", "labels[1]: question", "labels[1]: domains", "labels[1]: kind",
                   "labels[2]: needle", "labels[3]: survey", "labels[4]: duplicate id", "labels[4]: source"):
        assert needle in text


def test_top_level_must_hold_a_list():
    with pytest.raises(rl.LabelError):
        rl.parse_labels({"nope": 1})


def test_append_keeps_existing_and_numbers_new(tmp_path):
    path = tmp_path / "retrieval_labels.yaml"
    path.write_text(yaml.safe_dump(_doc(NEEDLE)), encoding="utf-8")
    new = [rl.RetrievalLabel(id="", question="Q2", domains=("papers",), kind="needle",
                             relevant=("d2_003",), source="synthetic")]
    added = rl.append_labels(path, new)
    assert [l.id for l in added] == ["r002"]
    assert [l.id for l in rl.load_labels(path)] == ["r001", "r002"]


def test_append_creates_the_file(tmp_path):
    path = tmp_path / "bench" / "retrieval_labels.yaml"
    new = [rl.RetrievalLabel(id="", question="Q", domains=("p",), kind="needle", relevant=("c",), source="synthetic")]
    rl.append_labels(path, new)
    assert [l.id for l in rl.load_labels(path)] == ["r001"]


def test_default_path_outside_a_vault(monkeypatch):
    monkeypatch.setattr(rl, "vault_dir", lambda: None)
    assert rl.default_path() is None
