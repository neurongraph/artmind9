"""Build one document's summary tree (spec 2026-11-01 §1.2) from its chunks.

`ask(step_name, prompt)` is the LLM: it returns the parsed JSON answer, or None
when the call failed or answered nothing usable; an exception counts as a
failure too. Step names are `chunk_summary` and `document_summary` (they reach
`llm_calls.log`). A chunk whose entry already matches its key in `prior` is kept
without a call. No file or graph I/O here: `ingest.extract_kg` and
`ingest.summarise_staged` read and write `summaries.json`.
"""
from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from loguru import logger

from artmind import summary_tree as st

Ask = Callable[[str, str], object]


def _ask(ask: Ask, step: str, prompt: str, limit: int) -> str | None:
    try:
        return st.parse_summary(ask(step, prompt), limit)
    except Exception as exc:  # a provider error must not fail the file
        logger.warning("Summary call {} failed: {}", step, exc)
        return None


def _reduce(summaries: list[str], *, ask: Ask, schema: dict, title: str | None,
            location: str | None = None) -> str | None:
    """Reduce until one summary is left: groups first while the parts are too long."""
    while sum(len(s) for s in summaries) > st.REDUCE_INPUT_CHARS:
        groups = st.reduce_groups(summaries, limit=st.REDUCE_INPUT_CHARS)
        if len(groups) == len(summaries):          # no group can merge: reduce what fits one call
            break
        reduced = []
        for group in groups:
            if len(group) == 1:
                reduced.append(group[0])
                continue
            text = _ask(ask, "document_summary",
                        st.reduce_prompt(schema, title=title, summaries=group, location=location),
                        st.DOC_SUMMARY_CHARS)
            if text is None:
                return None
            reduced.append(text)
        summaries = reduced
    if len(summaries) == 1:
        return summaries[0][: st.DOC_SUMMARY_CHARS]
    return _ask(ask, "document_summary",
                st.reduce_prompt(schema, title=title, summaries=summaries, location=location),
                st.DOC_SUMMARY_CHARS)


def build(chunks: list[dict], *, schema: dict, title: str | None, model: str, prior: dict | None,
          ask: Ask, workers: int = 1, location: str | None = None) -> tuple[dict, list[str]]:
    """`chunks`: [{"id", "text", "header_path"}] in document order. Returns the
    `summaries.json` data and the ids of chunks whose summary failed."""
    prior = prior if isinstance(prior, dict) else {}
    prior_chunks = {c.get("chunk_id"): c for c in prior.get("chunks") or [] if isinstance(c, dict)}
    work = [c for c in chunks if str(c.get("text") or "").strip()]
    count = len(work)
    keys = [st.chunk_key(model, c["text"]) for c in work]

    def one(index: int) -> dict | None:
        chunk, key = work[index], keys[index]
        old = prior_chunks.get(chunk["id"])
        if old and old.get("key") == key and old.get("summary"):
            return old
        prompt = st.chunk_prompt(schema, title=title, header_path=chunk.get("header_path"),
                                 seq=index + 1, count=count, text=chunk["text"])
        text = _ask(ask, "chunk_summary", prompt, st.CHUNK_SUMMARY_CHARS)
        return {"chunk_id": chunk["id"], "key": key, "summary": text} if text else None

    if workers > 1 and count > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(one, range(count)))
    else:
        results = [one(i) for i in range(count)]

    entries = [r for r in results if r is not None]
    failed = [work[i]["id"] for i, r in enumerate(results) if r is None]
    document = None
    if entries and not failed:
        doc_key = st.document_key(model, [e["key"] for e in entries])
        old_doc = prior.get("document") if isinstance(prior.get("document"), dict) else None
        if old_doc and old_doc.get("key") == doc_key and old_doc.get("summary"):
            document = old_doc
        else:
            text = _reduce([e["summary"] for e in entries], ask=ask, schema=schema, title=title,
                       location=location)
            document = {"key": doc_key, "summary": text} if text else None
    return {
        "version": st.FILE_VERSION,
        "model": model,
        "prompt_version": st.SUMMARY_PROMPT_VERSION,
        "document": document,
        "chunks": entries,
    }, failed
