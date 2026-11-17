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
    """`ARTMIND_OUTPUT_BUDGET` in characters; None when unset or 0 (off).

    With no `env`, the budget `_echo_json` will apply: under `serve` that is the caller's value
    for this request (`result_store.budget_override`), never the daemon's own environment. Reading
    `os.environ` there made the allocation plan for no budget and the echo cut the tail (round 8).
    """
    if env is None:
        from artmind import result_store

        try:
            return result_store.current_budget() or None
        except result_store.BudgetError as exc:
            raise ValueError(str(exc)) from None
    raw = env.get(BUDGET_ENV)
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


def replaced_refs(supersedes) -> list[dict]:
    """`[{name, valid_from}]` for the older documents a document replaced (empty when none)."""
    return [{"name": s.get("name"), "valid_from": s.get("valid_from")}
            for s in (supersedes or []) if isinstance(s, dict)]


def doc_ref(doc_id, title, name, domain, date, superseded, supersedes=None) -> dict:
    """The `doc` object of a brief passage row (`replaced` only when it replaced older versions)."""
    ref = {"id": doc_id, "title": doc_title(title, name), "domain": domain, "date": date or None,
           "status": doc_status(superseded)}
    if supersedes:
        ref["replaced"] = replaced_refs(supersedes)
    return ref


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


# A fragment ending in one of these is a title, not a sentence: "Dr. Lind" stays whole.
_TITLE_END = re.compile(r"(?:^|\s)(?:Dr|Mr|Mrs|Ms|Mx|Prof|St|Sr|Jr|Capt|Col|Gen|Lt|Rev|Hon)\.$")


def sentences(text) -> list[str]:
    flat = " ".join(str(text or "").split())
    if not flat:
        return []
    out: list[str] = []
    for part in _SENTENCE_SPLIT.split(flat):
        if not part:
            continue
        if out and _TITLE_END.search(out[-1]):
            out[-1] = f"{out[-1]} {part}"
        else:
            out.append(part)
    return out


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
        # Sources in order of preference (passage, chunk summary, document summary): the best
        # sentence of the first source that matches the question at all. A matching passage
        # sentence is evidence and beats a higher-scoring summary sentence, which is only a
        # guide (spec 2026-11-05 §4.1; the visibility gate measures exactly this).
        best = None
        for source, text in d.texts:
            best_score = 0.0
            for sentence in sentences(text):
                toks = _tokens(sentence)
                score = sum(w for f, w in idf.items() if f in toks) + sum(1 for s in special if s in toks)
                if score > best_score:        # strict: ties go to the earlier sentence
                    best_score, best = score, (source, sentence)
            if best is not None:
                break
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
    """`{doc_id: 'passage'|'gist'|'line'}` (spec §4.2). Every document is at least a line row.
    Up to `passage_docs` passage rows (needles first), then `gist_docs` gist rows, then more gist
    rows while `budget` allows; with a budget, no upgrade is made that would exceed it.
    `envelope` is the measured size of everything but the rows."""
    kind = {c.doc_id: "line" for c in cands}
    total = envelope + sum(c.line_chars for c in cands)

    def cost(c: Candidate, k: str) -> int:
        return {"line": c.line_chars, "gist": c.gist_chars, "passage": c.passage_chars}[k]

    def upgrade(c: Candidate, to: str) -> bool:
        """Upgrade unless it would break the budget: the line floor is the only hard floor,
        so every document stays listed and the `_echo_json` cut is a backstop, not the rule."""
        nonlocal total
        delta = cost(c, to) - cost(c, kind[c.doc_id])
        if budget and total + delta > budget:
            return False
        total += delta
        kind[c.doc_id] = to
        return True

    # Passage slots (at most `passage_docs`) go first to the needle set -- documents whose best
    # passage is within NEEDLE_TOLERANCE of the top, best relevance first, so a needle ranked
    # low by fusion still shows its text -- then by rank. When relevance is flat the needle set
    # is large; capping it at the slot count keeps a flat result from spending the budget on
    # passages.
    with_passage = [c for c in cands if c.has_passage]
    rels = [c.relevance for c in with_passage if c.relevance is not None]
    needles: list[Candidate] = []
    if rels:
        top = max(rels)
        needles = sorted((c for c in with_passage
                          if c.relevance is not None and c.relevance >= top - NEEDLE_TOLERANCE - 1e-9),
                         key=lambda c: (-c.relevance, c.rank))
    slots = passage_docs
    for c in [*needles, *with_passage]:
        if slots <= 0:
            break
        if kind[c.doc_id] == "line" and upgrade(c, "passage"):
            slots -= 1
    ordered = sorted(cands, key=lambda c: (-c.score, -c.hits, c.rank))
    gists = 0
    for c in ordered:
        if gists >= gist_docs:
            break
        if kind[c.doc_id] == "line" and upgrade(c, "gist"):
            gists += 1
    if budget:
        for c in ordered:
            if kind[c.doc_id] == "line" and not upgrade(c, "gist"):
                break
    return kind


def size(row: dict) -> int:
    return len(json.dumps(row, ensure_ascii=False, separators=(",", ":")))


def survey_row(kind: str, d: dict) -> dict:
    """One brief survey row of `kind` from the document facts `d` (see survey.py)."""
    line = {"rank": d["rank"], "doc_id": d["doc_id"], "title": d["title"], "date": d["date"],
            "status": d["status"], "score": d["score"], "matched": d["matched"], "hits": d["hits"]}
    if d.get("replaced"):
        line["replaced"] = d["replaced"]
    if kind == "line":
        return line
    row = {"rank": d["rank"], "doc_id": d["doc_id"], "title": d["title"], "domain": d["domain"], "date": d["date"],
           "status": d["status"], "score": d["score"], "gist": d["gist"], "gist_source": d["gist_source"],
           "matched": d["matched"], "hits": d["hits"], "best": d["best"]}
    if d.get("replaced"):
        row["replaced"] = d["replaced"]
    if kind == "passage":
        row["passage"] = d["passage"]
    return row


# --- graph rows (spec 2026-11-05 bounded-output §4, round 8 P58/P59) ----------------

#: A brief graph row's description: the first sentence, or this many characters.
ONE_LINE_CHARS = 160
#: Evidence ids a brief row lists before it keeps only the total.
EVIDENCE_IDS = 10
#: Subjects a brief insights finding lists before it keeps only the total.
FINDING_SUBJECTS = 10


def one_line(text, limit: int = ONE_LINE_CHARS) -> str | None:
    """The first sentence of `text`, or its first `limit` characters cut at a word boundary."""
    parts = sentences(text)
    if not parts:
        return None
    first = parts[0]
    return first if len(first) <= limit else cut(first, limit)


#: Property characters a brief row may carry (spec 2026-11-15 §5; plan question 9): an
#: entity-context or pattern2 row, and a row of the listing patterns.
ENTITY_PROPERTY_CHARS = 600
LISTING_PROPERTY_CHARS = 200
_LISTING_PATTERNS = frozenset({"pattern1", "pattern7", "pattern8"})


#: A string value is cut to the space left only when at least this many characters remain.
_MIN_CUT_CHARS = 24


def _bounded_properties(entity: dict, declared: list[str], budget: int) -> tuple[dict, dict, dict]:
    """Declared values in `declared` order within `budget` characters (compact JSON).

    Empty values (None, "", [], {}) are skipped. A list keeps its newest (last) items that fit; a
    string that does not fit is cut to the space left at a sentence or word boundary (`cut`); any
    other value (number, bool, mapping) that does not fit is left out.

    Returns (properties, truncated_properties, cut_properties):
    - truncated_properties: {list key: the list's full length} for every list not shown whole;
    - cut_properties: {key: full length in characters} for every string cut and every other value
      left out (a non-string's length is its compact JSON's)."""
    shown: dict = {}
    truncated: dict = {}
    cut_off: dict = {}
    for key in declared:
        value = entity.get(key)
        if value is None or value == "" or value == [] or value == {}:
            continue
        if isinstance(value, list):
            kept: list = []
            for item in reversed(value):
                if size({**shown, key: [item, *kept]}) > budget:
                    break
                kept.insert(0, item)
            if len(kept) < len(value):
                truncated[key] = len(value)
            if kept:
                shown[key] = kept
        elif size({**shown, key: value}) <= budget:
            shown[key] = value
        elif isinstance(value, str):
            room = budget - size({**shown, key: ""})
            text = cut(value, room) if room >= _MIN_CUT_CHARS else ""
            while text and size({**shown, key: text}) > budget:  # JSON escapes made it longer
                room -= size({**shown, key: text}) - budget
                text = cut(value, room) if room >= _MIN_CUT_CHARS else ""
            if text:
                shown[key] = text
            cut_off[key] = len(value)
        else:
            cut_off[key] = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    return shown, truncated, cut_off


def entity_line(entity: dict, declared: list[str] | None = None, budget: int = ENTITY_PROPERTY_CHARS) -> dict:
    """id, name, class and a one-line description of an entity's data (`entityData`), plus its
    declared non-temporal property values when `declared` (the class's key order) is given, with
    `truncated_properties` / `cut_properties` naming what the budget hid (`_bounded_properties`)."""
    entity = entity or {}
    out = {"id": entity.get("_id") or entity.get("id"), "name": entity.get("name"),
           "class": entity.get("entity_class"),
           "description": one_line(entity.get("description") or entity.get("summary"))}
    if declared:
        props, truncated, cut_off = _bounded_properties(entity, declared, budget)
        if props:
            out["properties"] = props
        if truncated:
            out["truncated_properties"] = truncated
        if cut_off:
            out["cut_properties"] = cut_off
    return out


def _id_list(items, key: str = "id") -> tuple[list[str], int]:
    ids = list(dict.fromkeys(str(i[key]) for i in (items or []) if isinstance(i, dict) and i.get(key)))
    return ids[:EVIDENCE_IDS], len(ids)


def _doc_line(doc: dict) -> dict:
    return {"id": doc.get("id"), "title": doc_title(doc.get("title"), doc.get("name")),
            "date": doc.get("valid_from") or doc.get("date") or None}


def pattern_row(pattern: str, row: dict, declared: list[str] | None = None) -> dict:
    """One brief row of pattern1 / pattern2 / pattern7 / pattern8: the entity line plus evidence ids and
    the totals the full row carried, never its nested lists. Of the property bag only the class's
    declared values (`declared`) are shown, within LISTING_PROPERTY_CHARS for the listing patterns
    and ENTITY_PROPERTY_CHARS for pattern2."""
    out = entity_line(row.get("entityData") or {}, declared,
                      LISTING_PROPERTY_CHARS if pattern in _LISTING_PATTERNS else ENTITY_PROPERTY_CHARS)
    if pattern == "pattern2":
        chunk_ids, n_chunks = _id_list(row.get("doc_sources"))
        docs = [_doc_line(d) for d in (row.get("source_documents") or []) if isinstance(d, dict)]
        out["chunk_ids"] = chunk_ids
        out["chunks_total"] = max(row.get("doc_sources_total") or 0, n_chunks)
        out["documents"] = docs[:EVIDENCE_IDS]
        out["documents_total"] = max(row.get("source_documents_total") or 0, len(docs))
    elif pattern == "pattern7":
        out["documents"] = [_doc_line(d) for d in (row.get("documents") or []) if isinstance(d, dict)]
        out["documents_total"] = row.get("document_count") or len(out["documents"])
    elif pattern == "pattern8":
        props = row.get("relProps") or {}
        if "linked" in row:  # --via mentions
            out["linked"] = row.get("linked")
            out["shared_chunks"] = row.get("shared_chunks")
            out["documents"] = row.get("documents") or []
            out["documents_total"] = row.get("documents_total", len(out["documents"]))
            out["chunk_ids"] = row.get("chunk_ids") or []
        else:
            out["rel_type"] = row.get("relType")
            out["observations"] = props.get("observation_count")
            out["documents"] = props.get("doc_count")
    return out


def entity_hint(domains, rows: list[dict]) -> str:
    """How to get a brief entity row's full form (the payload's `next`)."""
    scope = domains if isinstance(domains, str) else ",".join(domains)
    ids = [r.get("id") for r in rows if r.get("id")]
    one = f"artmind query entity-context --domain {scope} --entityId {ids[0] if ids else '<id>'} --shape brief"
    return f"one entity with its connections and sources: {one}; every key of these rows: rerun with --shape full"


def connection_line(c: dict) -> dict:
    t = c.get("target") or {}
    return {"rel_type": c.get("rel_type"), "direction": c.get("direction"), "id": t.get("id"),
            "name": t.get("name"), "class": t.get("entity_class"),
            "observations": c.get("observation_count"), "documents": c.get("doc_count")}


def summary_line(s: dict) -> str:
    return f"{s.get('rel_type')} {s.get('direction')}: {s.get('count')}"


def entity_context_row(row: dict, declared: list[str] | None = None) -> dict:
    """The brief form of an entity-context row (its `chunks` are already passage rows).

    Entity: id/name/class/one-line description. Every connection the command returned is kept
    (it is already capped by --maxConnections, 25 by default) but as an id/name/class line; the
    connection summary is one short string per type and direction; `more_chunks` and
    `source_documents` are id + title rows with their totals. A hub of 78 summaries, 25
    connections and 28 chunks stays well inside the 24,000-character agent budget
    (test_graph_brief.py measures it).

    With `declared` (the class's declared keys) the entity line also carries those values within
    ENTITY_PROPERTY_CHARS; its `truncated_properties` then also counts the lists the full row had
    already cut (the larger length per key). The row's own `truncated_properties` stays as it was,
    and the row's `cut` (`graph_query.entity_context_cut`) still lists only those: what the brief
    budget hid is on the entity line (`truncated_properties`, `cut_properties`), so a long value
    does not make an ordinary entity a `hub`."""
    more = row.get("more_chunks") or []
    out = {"entity": entity_line(row.get("entityData") or {}, declared)}
    if row.get("same_as"):
        out["same_as"] = row["same_as"]
    if row.get("truncated_properties"):
        out["truncated_properties"] = row["truncated_properties"]
        if declared:
            shown = out["entity"].get("truncated_properties") or {}
            for key, n in row["truncated_properties"].items():
                if key in declared:
                    shown[key] = max(shown.get(key) or 0, n)
            if shown:
                out["entity"]["truncated_properties"] = shown
    out["connection_summary"] = [summary_line(s) for s in row.get("connection_summary") or []]
    out["connections_total"] = row.get("connections_total")
    out["connections_matched"] = row.get("connections_matched")
    out["connections"] = [connection_line(c) for c in row.get("connections") or []]
    out["chunks_total"] = row.get("chunks_total")
    out["chunks"] = row.get("chunks") or []
    out["more_chunks_total"] = len(more)
    out["more_chunks"] = [{"id": m.get("id"), "title": m.get("name"), "doc_id": m.get("doc_id")} for m in more]
    out["source_documents_total"] = row.get("source_documents_total")
    out["source_documents"] = [_doc_line(d) for d in row.get("source_documents") or [] if isinstance(d, dict)]
    return out


def finding_row(f: dict) -> dict:
    """The brief form of one insights finding (`query insights show`): identity, score, title,
    subject ids, scalar stats; lists and dicts in stats/card reduced to counts."""
    subjects = f.get("subjects") or []
    stats = f.get("stats") or {}
    scalars = {k: v for k, v in stats.items() if v is None or isinstance(v, (bool, int, float))
               or (isinstance(v, str) and len(v) <= ONE_LINE_CHARS)}
    counts = {k: len(v) for k, v in stats.items() if isinstance(v, (list, dict))}
    card = f.get("card") or {}
    return {"key": f.get("key"), "detector": f.get("detector"), "kind": f.get("kind"),
            "score": f.get("score"), "title": f.get("title"), "diff": f.get("diff"), "action": f.get("action"),
            "subjects": [{"_id": s.get("_id"), "name": s.get("name"), "class": s.get("class")}
                         for s in subjects[:FINDING_SUBJECTS]],
            "subjects_total": len(subjects), "stats": scalars, "stats_counts": counts,
            "card_counts": {k: len(v) if isinstance(v, (list, dict)) else 1 for k, v in card.items()}}


# --- happenings (spec 2026-11-15 §5.2) -----------------------------------------------

HAPPENING_PARTICIPANTS = 5
HAPPENING_PLACES = 3
HAPPENING_CHUNK_IDS = 3


def happening_row(row: dict) -> dict:
    """A brief `query happenings` row: id, name, class, date, precision, type, up to 5
    participants, 3 places and 3 chunk ids (the ids `query chunks --idList` reads). The full
    row's `time_of_day` and `doc_ids` are dropped: the chunk ids lead to the text, and through
    it to the document."""
    row = row or {}
    return {"id": row.get("id"), "name": row.get("name"), "class": row.get("entity_class"),
            "date": row.get("date"), "precision": row.get("precision"), "type": row.get("type"),
            "participants": list(row.get("participants") or [])[:HAPPENING_PARTICIPANTS],
            "places": list(row.get("places") or [])[:HAPPENING_PLACES],
            "chunk_ids": list(row.get("chunk_ids") or [])[:HAPPENING_CHUNK_IDS]}
