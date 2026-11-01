import threading

from artmind import summary_build as sb
from artmind import summary_tree as st

SCHEMA = {"subject": "notes"}


class Ask:
    """Answers every chunk prompt with its chunk number and every reduce with 'DOC'."""

    def __init__(self, fail_on=()):
        self.calls = []
        self.fail_on = set(fail_on)
        self.lock = threading.Lock()

    def __call__(self, step, prompt):
        with self.lock:
            self.calls.append(step)
        if step == "chunk_summary":
            seq = int(prompt.split("This is chunk ")[1].split(" ")[0])
            if seq in self.fail_on:
                return None
            return {"summary": f"part {seq}"}
        return {"summary": "DOC"}


def _chunks(n, text="Some text."):
    return [{"id": f"c{i}", "text": text + str(i), "header_path": None} for i in range(1, n + 1)]


def test_every_chunk_gets_a_summary_and_the_document_is_reduced():
    ask = Ask()
    data, failed = sb.build(_chunks(3), schema=SCHEMA, title="T", model="m", prior=None, ask=ask)
    assert failed == []
    assert [c["chunk_id"] for c in data["chunks"]] == ["c1", "c2", "c3"]
    assert [c["summary"] for c in data["chunks"]] == ["part 1", "part 2", "part 3"]
    assert data["document"]["summary"] == "DOC"
    assert ask.calls.count("chunk_summary") == 3 and ask.calls.count("document_summary") == 1
    assert data["model"] == "m" and data["prompt_version"] == st.SUMMARY_PROMPT_VERSION
    assert data["version"] == st.FILE_VERSION


def test_a_one_chunk_document_reuses_its_chunk_summary_without_a_reduce():
    ask = Ask()
    data, _ = sb.build(_chunks(1), schema=SCHEMA, title="T", model="m", prior=None, ask=ask)
    assert data["document"]["summary"] == "part 1"
    assert "document_summary" not in ask.calls


def test_blank_chunks_are_skipped():
    chunks = _chunks(2) + [{"id": "blank", "text": "   ", "header_path": None}]
    data, _ = sb.build(chunks, schema=SCHEMA, title="T", model="m", prior=None, ask=Ask())
    assert [c["chunk_id"] for c in data["chunks"]] == ["c1", "c2"]


def test_a_failed_chunk_has_no_entry_and_no_document_summary():
    data, failed = sb.build(_chunks(3), schema=SCHEMA, title="T", model="m", prior=None, ask=Ask(fail_on={2}))
    assert failed == ["c2"]
    assert [c["chunk_id"] for c in data["chunks"]] == ["c1", "c3"]
    assert data["document"] is None


def test_an_ask_that_raises_counts_as_a_failure():
    def boom(step, prompt):
        raise RuntimeError("provider down")

    data, failed = sb.build(_chunks(2), schema=SCHEMA, title="T", model="m", prior=None, ask=boom)
    assert failed == ["c1", "c2"] and data["chunks"] == [] and data["document"] is None


def test_matching_prior_entries_are_reused_without_calls():
    first, _ = sb.build(_chunks(2), schema=SCHEMA, title="T", model="m", prior=None, ask=Ask())
    ask = Ask()
    again, failed = sb.build(_chunks(2), schema=SCHEMA, title="T", model="m", prior=first, ask=ask)
    assert failed == [] and ask.calls == []
    assert again == first


def test_a_changed_chunk_is_resummarised_and_the_document_reduced_again():
    first, _ = sb.build(_chunks(2), schema=SCHEMA, title="T", model="m", prior=None, ask=Ask())
    changed = _chunks(2)
    changed[1]["text"] = "Different text."
    ask = Ask()
    again, _ = sb.build(changed, schema=SCHEMA, title="T", model="m", prior=first, ask=ask)
    assert ask.calls == ["chunk_summary", "document_summary"]
    assert again["chunks"][0] == first["chunks"][0]


def test_another_model_invalidates_the_prior():
    first, _ = sb.build(_chunks(2), schema=SCHEMA, title="T", model="m", prior=None, ask=Ask())
    ask = Ask()
    sb.build(_chunks(2), schema=SCHEMA, title="T", model="m2", prior=first, ask=ask)
    assert ask.calls.count("chunk_summary") == 2


def test_long_documents_are_reduced_in_groups_first(monkeypatch):
    monkeypatch.setattr(st, "REDUCE_INPUT_CHARS", 12)    # "part N" is 6 chars: two per group
    ask = Ask()
    data, _ = sb.build(_chunks(5), schema=SCHEMA, title="T", model="m", prior=None, ask=ask)
    # 5 parts (30 chars) -> groups [1,2] [3,4] [5]: two group reduces (the lone [5]
    # needs none) -> "DOC", "DOC", "part 5" (12 chars, fits) -> one final reduce.
    assert ask.calls.count("chunk_summary") == 5
    assert ask.calls.count("document_summary") == 3
    assert data["document"]["summary"] == "DOC"


def test_workers_run_chunks_concurrently_but_keep_chunk_order():
    data, _ = sb.build(_chunks(6), schema=SCHEMA, title="T", model="m", prior=None, ask=Ask(), workers=4)
    assert [c["chunk_id"] for c in data["chunks"]] == [f"c{i}" for i in range(1, 7)]


def test_build_passes_location_to_reduce_prompt():
    """When location is given, reduce_prompt in the document_summary step receives it."""
    prompts = []

    class AskCapture:
        def __call__(self, step, prompt):
            prompts.append((step, prompt))
            if step == "chunk_summary":
                seq = int(prompt.split("This is chunk ")[1].split(" ")[0])
                return {"summary": f"part {seq}"}
            return {"summary": "DOC"}

    sb.build(_chunks(2), schema=SCHEMA, title="T", model="m", prior=None, ask=AskCapture(),
             location="AMD-7 rate uplift folder")
    reduce_prompts = [p for step, p in prompts if step == "document_summary"]
    assert reduce_prompts, "expected at least one document_summary prompt"
    assert "AMD-7 rate uplift folder" in reduce_prompts[0]


def test_build_without_location_omits_location_from_prompt():
    """build without location does not inject Location: into the reduce prompt."""
    prompts = []

    class AskCapture:
        def __call__(self, step, prompt):
            prompts.append((step, prompt))
            if step == "chunk_summary":
                return {"summary": "part"}
            return {"summary": "DOC"}

    sb.build(_chunks(2), schema=SCHEMA, title="T", model="m", prior=None, ask=AskCapture())
    reduce_prompts = [p for step, p in prompts if step == "document_summary"]
    assert reduce_prompts
    assert "Location:" not in reduce_prompts[0]
