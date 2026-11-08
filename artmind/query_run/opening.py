"""Opened documents (P3 spec §6.5): which listed documents `query run` reads before it answers.

After the program has run, the top documents by fused rank that carry no full passage yet (`OPEN_TOP`) and
the listed documents whose title carries a term the question names (`OPEN_TITLE`) are opened with one
`survey --shape brief --expand` step. This module only chooses and builds the command; the executor runs
it and the bundle places what came back. No I/O, no LLM.
"""

from __future__ import annotations

import re

OPEN_TOP = 5
OPEN_TITLE = 2
OPEN_ID = "open"
WORD_MIN = 5

# Question words that are not distinctive even at 5+ letters.
_EXTRA_STOPWORDS = frozenset("""
about above after again could every other please should their there these those through under which
while would tell describe explain list give show state between during against whether where
""".split())

_YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
_ISO = re.compile(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)")
_VERSION = re.compile(r"(?<![a-z0-9])v\d+(?![a-z0-9])", re.IGNORECASE)
_TAG = re.compile(r"_\d{4}_\d{2}(?!\d)")
_WORD = re.compile(r"[A-Za-z]{%d,}" % WORD_MIN)


def _stopwords() -> frozenset:
    from artmind.query_plan.classify.lexical import STOPWORDS

    return frozenset(STOPWORDS) | _EXTRA_STOPWORDS


def normalise(text) -> str:
    """Casefolded, `_` and `-` read as spaces, one space between tokens."""
    return " ".join(re.sub(r"[_\-]", " ", str(text or "").casefold()).split())


def title_terms(question: str) -> list[str]:
    """Terms the question names that a document title may carry, normalised, in question order."""
    q = str(question or "")
    found: list[tuple[int, str]] = []
    for pattern in (_ISO, _YEAR, _VERSION, _TAG):
        found += [(m.start(), m.group(0)) for m in pattern.finditer(q)]
    stop = _stopwords()
    found += [(m.start(), m.group(0)) for m in _WORD.finditer(q) if m.group(0).casefold() not in stop]
    out: list[str] = []
    for _, term in sorted(found):
        norm = normalise(term)
        if norm and norm not in out:
            out.append(norm)
    return out


def _matches(term: str, title: str) -> bool:
    if term.replace(" ", "").isalpha():  # a word: allow a plural either way
        stem = term[:-1] if term.endswith("s") and len(term) > WORD_MIN else term
        return re.search(r"(?<![a-z0-9])" + re.escape(stem), title) is not None
    return re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", title) is not None


def _passaged(evidence) -> set:
    return {(e.get("doc") or {}).get("id") for e in evidence or [] if e.get("kind") == "passage"}


def skip_reason(ranked_documents, evidence, question, fact_docs=None) -> str | None:
    """Why nothing is opened, or None when something qualifies."""
    if not [d for d in ranked_documents or [] if d.get("doc_id")] and not fact_docs:
        return "no_documents"
    if not select_documents(ranked_documents, evidence, question, fact_docs=fact_docs):
        return "all_passaged"
    return None


FACT_TOP = 20   # facts, in rank order, whose source documents may take a rank slot


def fact_documents(facts, evidence=(), *, top: int = FACT_TOP) -> list[str]:
    """Source document ids of the first `top` facts, in fact order, deduplicated (spec 6.5 rule 4).

    A fact's own `doc_ids` come first; its `chunk_ids` are mapped through the evidence rows that know them.
    """
    by_chunk = {e.get("chunk_id"): (e.get("doc") or {}).get("id") for e in evidence or [] if e.get("chunk_id")}
    out: list[str] = []
    for f in list(facts or [])[:top]:
        for doc_id in [*(f.get("doc_ids") or []), *(by_chunk.get(c) for c in f.get("chunk_ids") or [])]:
            if doc_id and doc_id not in out:
                out.append(doc_id)
    return out


def _interleave(first: list[str], second: list[str], top: int) -> list[str]:
    out: list[str] = []
    for i in range(max(len(first), len(second))):
        for lst in (first, second):
            if i < len(lst) and lst[i] not in out:
                out.append(lst[i])
    return out[:top]


def select_documents(ranked_documents, evidence, question, *, top: int = OPEN_TOP,
                     title: int = OPEN_TITLE, fact_docs=None) -> list[tuple[str, str]]:
    """[(doc_id, "rank" | "title")]. `ranked_documents` are best first, rows with `doc_id` and `title`.

    `fact_docs` (ids behind the top facts) alternate with the ranked documents in the rank slots.
    """
    docs = [d for d in ranked_documents or [] if d.get("doc_id")]
    have = _passaged(evidence)
    ranked = [d["doc_id"] for d in docs if d["doc_id"] not in have]
    behind = [i for i in fact_docs or [] if i not in have]
    chosen: list[tuple[str, str]] = [(i, "rank") for i in _interleave(ranked, behind, top)]
    taken = {d for d, _ in chosen}
    terms = title_terms(question)
    if terms and title > 0:
        scored = []
        for rank, d in enumerate(docs):
            if d["doc_id"] in taken:
                continue
            norm = normalise(d.get("title"))
            hits = sum(1 for t in terms if _matches(t, norm))
            if hits:
                scored.append((-hits, rank, d["doc_id"]))
        chosen += [(doc_id, "title") for _, _, doc_id in sorted(scored)[:title]]
    return chosen


def command(doc_ids, domains, question) -> list[str]:
    """argv of the one open step."""
    argv = ["query", "survey", "--shape", "brief", "--domain", ",".join(domains)]
    for doc_id in doc_ids:
        argv += ["--expand", str(doc_id)]
    argv.append("--compact")
    if str(question).startswith("-"):
        argv.append("--")
    argv.append(str(question))
    return argv


def next_unopened(documents, *, limit: int = OPEN_TOP) -> list[str]:
    """Ids of the next `limit` listed documents by rank that were not opened."""
    return [d["doc_id"] for d in documents or [] if d.get("doc_id") and not d.get("opened")][:limit]
