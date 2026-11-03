"""`ingest summarise --dryRun`: counts the calls from the staged files; asks and writes nothing."""
import json

from click.testing import CliRunner

import artmind.ingest as ing
from _dry_run import assert_effect_shape
from artmind import summary_tree as st
from artmind.cli import cli
from test_ingest_summarise import CHUNKS, _env, _stage


def _run(*args):
    result = CliRunner().invoke(cli, ["ingest", "summarise", "--domain", "notes", *args, "--compact"])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_dry_run_asks_nothing_and_matches_the_real_effect(monkeypatch, tmp_path):
    kg, committed = _env(monkeypatch, tmp_path)
    folder = _stage(kg, "notes", "a", CHUNKS)
    _stage(kg, "notes", "b", [{"id": "x1", "doc_id": "doc-b", "text": "Only chunk."}])
    calls = []
    real_llm = ing._summary_llm
    monkeypatch.setattr(ing, "_summary_llm", lambda *a, **k: calls.append(1) or real_llm(*a, **k))

    dry = _run("--dryRun")

    assert calls == [] and committed == [] and not (folder / st.SUMMARIES_FILE).exists()
    assert dry["dry_run"] is True
    assert_effect_shape(dry["effect"], llm_calls=4)  # 2 chunks + 1 reduce, then 1 chunk
    assert dry["effect"]["llm"]["model"] == "m"
    assert dry["effect"]["changes"] == [{"kind": "summary_write", "count": 2, "sample": ["a.md", "b.md"]}]
    assert dry["effect"]["records"] == [".artmind/data/kg/notes"]

    real = _run()

    assert sorted(committed) == ["a", "b"]
    assert real["dry_run"] is False
    assert real["effect"] == dry["effect"]


def test_up_to_date_documents_are_no_effect(monkeypatch, tmp_path):
    kg, _ = _env(monkeypatch, tmp_path)
    _stage(kg, "notes", "a", CHUNKS)
    _run()

    dry = _run("--dryRun")

    assert dry["effect"]["changes"] == [] and dry["effect"]["llm"]["calls"] == 0
    assert dry["effect"]["llm"]["model"] is None
