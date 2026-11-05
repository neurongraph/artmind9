"""The brief row shape (spec 2026-11-05 bounded-output §4): short, question-aware
rows for the retrieval commands the agent uses (`--shape brief`).

Pure: no Neo4j, no LLM, no file I/O. The gist is extractive (a sentence copied
verbatim from the passage, chunk summary or document summary, chosen by how well
it matches the question's terms); the allocation decides, deterministically,
which survey documents get a passage row, a gist row or only a line.
"""
from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass, field

SHAPES = ("brief", "full")
PASSAGE_CHARS = 1200
SECOND_PASSAGE_CHARS = 400
GIST_CHARS = 220
NEEDLE_TOLERANCE = 0.03
#: Characters reserved for the envelope (command, warnings, parameters, topics).
ENVELOPE_CHARS = 1000

BUDGET_ENV = "ARTMIND_OUTPUT_BUDGET"

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])")
_WORD = re.compile(r"[a-z0-9]+")
_RAW_WORD = re.compile(r"[A-Za-z0-9]+")


def output_budget(env: dict | None = None) -> int | None:
    """`ARTMIND_OUTPUT_BUDGET` in characters; None when unset or 0 (off)."""
    raw = (os.environ if env is None else env).get(BUDGET_ENV)
    if raw is None or not str(raw).strip():
        return None
    try:
        value = int(str(raw).strip())
    except ValueError:
        raise ValueError(f"{BUDGET_ENV} must be a non-negative integer (characters; 0 = off), got {raw!r}") from None
    if value < 0:
        raise ValueError(f"{BUDGET_ENV} must be a non-negative integer (characters; 0 = off), got {value}")
    return value or None


# --- small helpers --------------------------------------------------------------


def cut(text, limit: int) -> str:
    """`text` on one line, at most `limit` characters, cut at a sentence boundary or
    whitespace the way summaries are (`summary_tree.parse_summary`)."""
    from artmind.summary_tree import parse_summary

    return parse_summary({"summary": text}, limit) or ""


def doc_title(title, name) -> str | None:
    """The title, else the name without its extension (as survey's coalesce does)."""
    if title:
        return str(title)
    if not name:
        return None
    name = str(name)
    stem, dot, ext = name.rpartition(".")
    return stem if dot and stem and 1 <= len(ext) <= 5 and " " not in ext else name


def doc_status(superseded) -> str:
    return "superseded" if superseded else "current"


def doc_ref(doc_id, title, name, domain, date, superseded) -> dict:
    """The `doc` object of a brief passage row."""
    return {"id": doc_id, "title": doc_title(title, name), "domain": domain, "date": date or None,
            "status": doc_status(superseded)}


def passage_row(rank: int, passage_id, chunk_id, text, relevance, doc: dict, *, second: str | None = None) -> dict:
    row = {"rank": rank, "passage_id": passage_id, "chunk_id": chunk_id, "text": cut_keep(text, PASSAGE_CHARS),
           "relevance": relevance, "doc": doc}
    if second:
        row["second_passage"] = cut_keep(second, SECOND_PASSAGE_CHARS)
    return row


def cut_keep(text, limit: int) -> str:
    """Like `cut` but keeps line breaks of a short text (a passage is shown as written)."""
    text = str(text or "")
    return text if len(text) <= limit else cut(text, limit)


# --- question terms and the gist ----------------------------------------------------


def fold(word: str) -> str:
    """Lowercase plural fold: no stemming dependency, just trailing -s/-es/-ies."""
    w = word.lower()
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 4 and w.endswith(("sses", "xes", "zes", "ches", "shes")):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return w[:-1]
    return w


def question_terms(question: str) -> list[str]:
    """Question words after stopword removal, the terms survey's keyword legs use."""
    from artmind.graph_query import sanitize_lucene_query

    seen: list[str] = []
    for word in _WORD.findall(sanitize_lucene_query(question, drop_stopwords=True).lower()):
        if word not in seen:
            seen.append(word)
    return seen


def special_tokens(question: str) -> set[str]:
    """Names, numbers and dates in the question: tokens with a digit, and capitalised
    words that do not open the question."""
    out: set[str] = set()
    for i, word in enumerate(_RAW_WORD.findall(question)):
        named = i > 0 and word[:1].isupper()
        if named or any(ch.isdigit() for ch in word):
            out.add(fold(word))
    return out


def sentences(text) -> list[str]:
    flat = " ".join(str(text or "").split())
    return [s for s in _SENTENCE_SPLIT.split(flat) if s] if flat else []


def _tokens(text: str) -> set[str]:
    return {fold(w) for w in _WORD.findall(text.lower())}


@dataclass
class GistInput:
    doc_id: str
    #: (source, text) in order of preference: passage, chunk_summary, document_summary.
    texts: list[tuple[str, str]] = field(default_factory=list)
    title: str | None = None


def build_gists(question: str, docs: list[GistInput]) -> dict[str, dict]:
    """`{doc_id: {gist, gist_source, matched}}` for every document of a result set."""
    terms = question_terms(question)
    folded = {t: fold(t) for t in terms}
    special = special_tokens(question)
    doc_tokens = {d.doc_id: set().union(_tokens(d.title or ""), *(_tokens(t) for _, t in d.texts)) for d in docs}
    n = max(len(docs), 1)
    idf = {}
    for t, f in folded.items():
        df = sum(1 for toks in doc_tokens.values() if f in toks)
        if df:
            idf[f] = math.log(1 + n / df)
    out: dict[str, dict] = {}
    for d in docs:
        matched = [t for t in terms if folded[t] in doc_tokens[d.doc_id]]
        best_score, best = 0.0, None
        for source, text in d.texts:
            for sentence in sentences(text):
                toks = _tokens(sentence)
                score = sum(w for f, w in idf.items() if f in toks) + sum(1 for s in special if s in toks)
                if score > best_score:        # strict: ties go to the earlier source
                    best_score, best = score, (source, sentence)
        if best is None:
            best = _fallback(d)
            matched = []
        gist, source = (cut(best[1], GIST_CHARS), best[0]) if best else ("", None)
        out[d.doc_id] = {"gist": gist, "gist_source": source, "matched": matched}
    return out


def _fallback(d: GistInput):
    """Nothing matched a term: the document summary's first sentence (its purpose)."""
    for wanted in ("document_summary", "chunk_summary", "passage"):
        for source, text in d.texts:
            if source == wanted and sentences(text):
                return source, sentences(text)[0]
    return None


# --- allocation -------------------------------------------------------------------


@dataclass
class Candidate:
    doc_id: str
    rank: int                 # 1-based
    score: float
    hits: int                 # passages + chunk summaries matched
    relevance: float | None   # best-passage cosine
    has_passage: bool
    line_chars: int
    gist_chars: int
    passage_chars: int


def allocate(cands: list[Candidate], *, passage_docs: int, gist_docs: int, budget: int | None,
             envelope: int = ENVELOPE_CHARS) -> dict[str, str]:
    """`{doc_id: 'passage'|'gist'|'line'}` (spec §4.2). Floors (needle rule, passage_docs,
    gist_docs) are always met; beyond them gist rows fill what `budget` leaves."""
    kind = {c.doc_id: "line" for c in cands}
    total = envelope + sum(c.line_chars for c in cands)

    def upgrade(c: Candidate, to: str) -> None:
        nonlocal total
        cost = {"line": c.line_chars, "gist": c.gist_chars, "passage": c.passage_chars}
        total += cost[to] - cost[kind[c.doc_id]]
        kind[c.doc_id] = to

    rels = [c.relevance for c in cands if c.has_passage and c.relevance is not None]
    if rels:
        top = max(rels)
        for c in cands:
            if c.has_passage and c.relevance is not None and c.relevance >= top - NEEDLE_TOLERANCE - 1e-9:
                upgrade(c, "passage")
    for c in cands:
        if sum(1 for k in kind.values() if k == "passage") >= passage_docs:
            break
        if kind[c.doc_id] == "line" and c.has_passage:
            upgrade(c, "passage")
    ordered = sorted(cands, key=lambda c: (-c.score, -c.hits, c.rank))
    for c in ordered:
        if sum(1 for k in kind.values() if k == "gist") >= gist_docs:
            break
        if kind[c.doc_id] == "line":
            upgrade(c, "gist")
    if budget:
        for c in ordered:
            if kind[c.doc_id] != "line":
                continue
            if total + c.gist_chars - c.line_chars > budget:
                break
            upgrade(c, "gist")
    return kind


def size(row: dict) -> int:
    return len(json.dumps(row, ensure_ascii=False, separators=(",", ":")))


def survey_row(kind: str, d: dict) -> dict:
    """One brief survey row of `kind` from the document facts `d` (see survey.py)."""
    line = {"rank": d["rank"], "doc_id": d["doc_id"], "title": d["title"], "date": d["date"],
            "status": d["status"], "score": d["score"], "matched": d["matched"], "hits": d["hits"]}
    if kind == "line":
        return line
    row = {"rank": d["rank"], "doc_id": d["doc_id"], "title": d["title"], "domain": d["domain"], "date": d["date"],
           "status": d["status"], "score": d["score"], "gist": d["gist"], "gist_source": d["gist_source"],
           "matched": d["matched"], "hits": d["hits"], "best": d["best"]}
    if kind == "passage":
        row["passage"] = d["passage"]
    return row
