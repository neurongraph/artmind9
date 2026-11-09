"""Miss records (spec 2026-11-08 §4.1): what knowledge a turn was missing, derived from stored traces.

One record per miss, kinds: vocabulary, reformulation, identity, scope, structure, answer, user-stated. Pure
detectors over span dicts (as `store.list_spans` returns them); `load_and_derive` reads the registry. No LLM call,
no graph. Records hold private corpus text: write them only under `<ARTMIND_HOME>/logs/misses/`, never to stdout.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from artmind.traces import analysis
from artmind.traces import path as path_mod

KINDS = ("vocabulary", "reformulation", "identity", "scope", "structure", "answer", "user-stated")
SEARCH_COMMANDS = frozenset({"query vector-text", "query survey"})


@dataclass(frozen=True)
class Miss:
    kind: str
    trace_id: str
    detail: dict = field(default_factory=dict)
    span_id: str | None = None
    run_id: str | None = None
    seq: int | None = None
    qid: str | None = None

    def to_dict(self) -> dict:
        return {"kind": self.kind, "trace_id": self.trace_id, "run_id": self.run_id, "seq": self.seq,
                "qid": self.qid, "span_id": self.span_id, "detail": self.detail}


MIN_INLINE = 6


def _doc_entries(span: dict) -> list[dict]:
    ev = span.get("evidence") or {}
    out = [d for d in ev.get("documents") or [] if isinstance(d, dict)]
    prov = ev.get("provenance") if isinstance(ev.get("provenance"), dict) else {}
    out += [d for d in prov.get("documents") or [] if isinstance(d, dict)]
    return out


def _doc_map(spans: list[dict]) -> dict[str, tuple[str | None, str | None]]:
    """doc_id -> (path, title) from every span's documents that names one (brief-shape spans carry ids only)."""
    out: dict[str, tuple[str | None, str | None]] = {}
    for s in spans:
        for d in _doc_entries(s):
            path, title = d.get("source_path") or d.get("path"), d.get("title")
            if d.get("doc_id") and (path or title):
                old = out.get(d["doc_id"], (None, None))
                out[d["doc_id"]] = (old[0] or path, old[1] or title)
    return out


def _span_documents(span: dict, idmap: dict | None = None) -> list[tuple[str | None, str | None]]:
    """(path, title) pairs a span returned; a document with only a doc_id resolves through `idmap`."""
    out = []
    for d in _doc_entries(span):
        path, title = d.get("source_path") or d.get("path"), d.get("title")
        if not (path or title) and idmap and d.get("doc_id") in idmap:
            path, title = idmap[d["doc_id"]]
        out.append((path, title))
    return out


def _returned(span: dict, cited: str, idmap: dict | None = None) -> bool:
    for held, title in _span_documents(span, idmap):
        if isinstance(held, str) and held and path_mod._same_document(cited, held):
            return True
        if isinstance(title, str) and title and title.casefold() == cited.casefold():
            return True
    return False


def _mentioned(answer: str, path: str | None, title: str | None) -> bool:
    low = answer.casefold()
    if isinstance(path, str) and path:
        stem = path.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0].casefold()
        if len(stem) >= MIN_INLINE and (stem in low or stem.replace("_", " ").replace("-", " ") in low):
            return True
    return isinstance(title, str) and len(title) >= MIN_INLINE and title.casefold() in low


def cited_spans(answer: str | None, spans: list[dict]) -> dict[str, str | None]:
    """{cited source: span_id of the first span (by start time) that returned it, or None}.

    Sources-section links when the answer has them; otherwise every document any span held whose file stem or
    title the answer names inline (keys are then the document paths, or the title when a row has no path)."""
    ordered = sorted(spans, key=lambda s: s.get("started_at") or 0.0)
    idmap = _doc_map(ordered)
    links: dict[str, str | None] = {}
    sources = path_mod.parse_sources(answer)
    if sources:
        for cited in sources:
            links[cited] = next((s.get("span_id") for s in ordered if _returned(s, cited, idmap)), None)
        return links
    if not answer:
        return links
    for s in ordered:
        for path, title in _span_documents(s, idmap):
            # `--shape brief` rows often carry a title and no path (a journal entry titled by its date): key by
            # the title then; `_returned` matches a title key too.
            key = path if isinstance(path, str) and path else title if isinstance(title, str) and title else None
            if key and key not in links and _mentioned(answer, path, title):
                links[key] = next((x.get("span_id") for x in ordered if _returned(x, key, idmap)), None)
    return links


STOPWORDS = frozenset(
    "the and for with that this what when where which who whom whose why how are was were has have had not "
    "but from into onto over under about after before during each every any all some our your their his her "
    "its they them you can could would should will shall may might must did does doing done than then there "
    "these those also just only very more most such per via".split())
_WORD = re.compile(r"[a-z0-9]+")


def _words(text: str | None) -> list[str]:
    seen: dict[str, None] = {}
    for w in _WORD.findall((text or "").casefold()):
        if len(w) >= 3 and w not in STOPWORDS:
            seen.setdefault(w, None)
    return list(seen)


def _stem(word: str) -> str:
    for suffix in ("ing", "es", "ed", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def _present(word: str, question_words: set[str]) -> bool:
    stem = _stem(word)
    return word in question_words or any(stem == _stem(q) for q in question_words)


def search_phrase(span: dict) -> str | None:
    if span.get("command") not in SEARCH_COMMANDS:
        return None
    args = span.get("args") if isinstance(span.get("args"), dict) else {}
    phrase = args.get("question") or args.get("query")
    return phrase if isinstance(phrase, str) and phrase.strip() else None


def _search_spans(spans: list[dict]) -> list[dict]:
    return [s for s in sorted(spans, key=lambda s: s.get("started_at") or 0.0) if search_phrase(s)]


def vocabulary(question: str, answer: str | None, spans: list[dict], *, trace_id: str = "") -> list[Miss]:
    links = cited_spans(answer, spans)
    by_span: dict[str, list[str]] = {}
    for cited, sid in links.items():
        if sid:
            by_span.setdefault(sid, []).append(cited)
    qwords = set(_words(question))
    out = []
    for s in _search_spans(spans):
        if s.get("span_id") not in by_span:
            continue
        added = [w for w in _words(search_phrase(s)) if not _present(w, qwords)]
        if added:
            out.append(Miss("vocabulary", trace_id, {"phrase": search_phrase(s), "added_terms": added,
                                                      "sources": by_span[s["span_id"]]}, span_id=s.get("span_id")))
    return out


def reformulation(question: str, answer: str | None, spans: list[dict], *, trace_id: str = "") -> list[Miss]:
    winners = {sid for sid in cited_spans(answer, spans).values() if sid}
    searches = _search_spans(spans)
    for i, s in enumerate(searches):
        if s.get("span_id") in winners:
            if i == 0:
                return []
            return [Miss("reformulation", trace_id, {"attempts": i + 1,
                                                     "phrases": [search_phrase(p) for p in searches[:i]],
                                                     "winning_phrase": search_phrase(s)},
                         span_id=s.get("span_id"))]
    return []


LOW_SCORE = 0.6
IDENTITY_COMMANDS = frozenset({"query entity-resolve", "query resolve-key"})


def _identity_reason(span: dict) -> dict | None:
    ev = span.get("evidence") or {}
    args = span.get("args") if isinstance(span.get("args"), dict) else {}
    name = args.get("name") or args.get("question")
    if ev.get("status") == "no_match":
        return {"reason": "no_match", "name": name}
    if ev.get("status") == "needs_disambiguation":
        return {"reason": "ambiguous", "name": name}
    resolved = [r for r in ev.get("resolved") or [] if isinstance(r, dict)]
    if not resolved:
        return None
    by_name: dict[str, list[str]] = {}
    for r in resolved:
        if isinstance(r.get("name"), str) and r.get("id"):
            by_name.setdefault(r["name"].casefold(), []).append(r["id"])
    twins = next((ids for ids in by_name.values() if len(set(ids)) > 1), None)
    if twins:
        return {"reason": "twins", "name": name, "ids": twins}
    top = resolved[0].get("score")
    if isinstance(top, (int, float)) and top < LOW_SCORE:
        return {"reason": "low_score", "name": name, "score": top}
    if analysis._ambiguous(span):
        return {"reason": "ambiguous", "name": name}
    return None


def identity(spans: list[dict], *, trace_id: str = "") -> list[Miss]:
    out = []
    for s in sorted(spans, key=lambda s: s.get("started_at") or 0.0):
        if s.get("command") in IDENTITY_COMMANDS:
            reason = _identity_reason(s)
            if reason:
                out.append(Miss("identity", trace_id, reason, span_id=s.get("span_id")))
    return out


def scope(answer: str | None, spans: list[dict], *, trace_id: str = "") -> list[Miss]:
    ordered = sorted(spans, key=lambda s: s.get("started_at") or 0.0)
    out = []
    for s in ordered:
        for w in (s.get("evidence") or {}).get("warnings") or []:
            if isinstance(w, str) and w.startswith("unknown domain"):
                out.append(Miss("scope", trace_id, {"reason": "unknown_domain", "warning": w},
                                span_id=s.get("span_id")))
    first_by_command: dict[str, set[str]] = {}
    widened: set[str] = set()
    for s in ordered:
        domains = path_mod.span_domains(s)
        command = s.get("command") or ""
        if not domains or path_mod.layer_of(command) == "discovery":
            continue
        first = first_by_command.setdefault(command, domains)
        if domains > first and command not in widened:
            widened.add(command)
            out.append(Miss("scope", trace_id, {"reason": "widened", "command": command,
                                                "from": sorted(first), "to": sorted(domains)},
                            span_id=s.get("span_id")))
    searches = _search_spans(ordered)
    first_scope = path_mod.span_domains(searches[0]) if searches else None
    if first_scope:
        by_id = {s.get("span_id"): s for s in ordered}
        for cited, sid in cited_spans(answer, ordered).items():
            held = path_mod.span_domains(by_id[sid]) if sid else None
            if held and not any(path_mod._covers(f, d) for d in held for f in first_scope):
                out.append(Miss("scope", trace_id, {"reason": "outside_first_scope", "source": cited,
                                                    "first_scope": sorted(first_scope), "span_scope": sorted(held)},
                                span_id=sid))
    return out


FOLLOW_UP = re.compile(
    r"\b(?:you (?:missed|forgot|left out|did ?n[o']t (?:mention|include))|what about|"
    r"(?:also|please) (?:include|add|mention)|(?:is|was|are|were) missing)\b[:,]?\s*(.*)",
    re.IGNORECASE | re.DOTALL)
ITEM_CHARS = 200


def structure(answer: str | None, spans: list[dict], *, trace_id: str = "") -> list[Miss]:
    out = []
    for s in spans:
        if (s.get("evidence") or {}).get("complete") is False:
            out.append(Miss("structure", trace_id, {"reason": "incomplete_count", "command": s.get("command")},
                            span_id=s.get("span_id")))
    graph_ran = any(path_mod.span_layer(s) in path_mod.GRAPH_LAYERS for s in spans)
    if not graph_ran and path_mod.answer_basis(answer, spans) == "vector":
        names = []
        for s in spans:
            if s.get("command") in IDENTITY_COMMANDS and _identity_reason(s) is None:
                top = ((s.get("evidence") or {}).get("resolved") or [{}])[0]
                if isinstance(top.get("name"), str):
                    names.append(top["name"])
        if names:
            out.append(Miss("structure", trace_id, {"reason": "text_despite_entity", "entities": names}))
    return out


def answer(grade: dict | None, *, trace_id: str = "") -> list[Miss]:
    if not isinstance(grade, dict):
        return []
    g = grade.get("grade")
    out = [Miss("answer", trace_id, {"item": item, "grade": g}) for item in grade.get("must_missed") or []]
    out += [Miss("answer", trace_id, {"must_not": item, "grade": g}) for item in grade.get("must_not_hit") or []]
    return out


def user_stated(feedback: dict | None, next_question: str | None, *, trace_id: str = "") -> list[Miss]:
    out = []
    if isinstance(feedback, dict) and feedback.get("rating") in ("partial", "wrong"):
        item = feedback.get("expected") or feedback.get("note")
        if item:
            out.append(Miss("user-stated", trace_id, {"source": "feedback", "rating": feedback["rating"],
                                                      "item": item}))
    match = FOLLOW_UP.search(next_question or "")
    if match and match.group(1).strip():
        out.append(Miss("user-stated", trace_id, {"source": "follow_up",
                                                  "item": match.group(1).strip()[:ITEM_CHARS]}))
    return out


def derive_turn(turn: dict, spans: list[dict], *, grade: dict | None = None, feedback: dict | None = None,
                next_question: str | None = None, qid: str | None = None) -> list[Miss]:
    tid = turn["trace_id"]
    q, a = turn.get("question") or "", turn.get("answer")
    found = [*vocabulary(q, a, spans, trace_id=tid), *reformulation(q, a, spans, trace_id=tid),
             *identity(spans, trace_id=tid), *scope(a, spans, trace_id=tid), *structure(a, spans, trace_id=tid),
             *answer(grade, trace_id=tid), *user_stated(feedback, next_question, trace_id=tid)]
    stamp = {"run_id": turn.get("benchmark_run_id"), "seq": turn.get("benchmark_seq"), "qid": qid}
    return [Miss(m.kind, m.trace_id, m.detail, m.span_id, **stamp) for m in found]


def _latest_grade(conn, run_id: str | None, seq: int | None) -> dict | None:
    if not run_id or seq is None:
        return None
    row = conn.execute("SELECT detail FROM trace_grades WHERE run_id = ? AND seq = ? ORDER BY created_at DESC"
                       " LIMIT 1", (run_id, seq)).fetchone()
    return json.loads(row[0]) if row else None


def load_and_derive(*, since: float | None = None, run_refs=(), source: str | None = None
                    ) -> tuple[list[Miss], list[dict]]:
    from artmind.traces import compare, store

    conn = store._connect()
    try:
        run_ids = {compare._resolve(conn, ref) for ref in run_refs}
    finally:
        conn.close()
    turns = store.list_turns(source=source, since=since)
    if run_ids:
        turns = [t for t in turns if t.get("benchmark_run_id") in run_ids]
    spans_by = store.list_spans_for([t["trace_id"] for t in turns])
    records: list[Miss] = []
    conn = store._connect()
    try:
        for t in turns:
            nxt = None
            if t.get("session_id") and t.get("turn_index"):
                row = conn.execute("SELECT question FROM trace_turns WHERE session_id = ? AND turn_index = ?",
                                   (t["session_id"], t["turn_index"] + 1)).fetchone()
                nxt = row[0] if row else None
            qid = None
            if t.get("benchmark_run_id"):
                row = conn.execute("SELECT qid FROM benchmark_questions WHERE run_id = ? AND seq = ?",
                                   (t["benchmark_run_id"], t.get("benchmark_seq"))).fetchone()
                qid = row[0] if row else None
            records += derive_turn(t, spans_by.get(t["trace_id"], []),
                                   grade=_latest_grade(conn, t.get("benchmark_run_id"), t.get("benchmark_seq")),
                                   feedback=store.latest_feedback(t["trace_id"]), next_question=nxt, qid=qid)
    finally:
        conn.close()
    return records, turns


def summarise(records: list[Miss], turns: list[dict]) -> dict:
    by_kind = {k: 0 for k in KINDS}
    by_reason: dict[str, dict[str, int]] = {"identity": {}, "scope": {}, "structure": {}}
    for m in records:
        by_kind[m.kind] = by_kind.get(m.kind, 0) + 1
        if m.kind in by_reason:
            reason = m.detail.get("reason", "other")
            by_reason[m.kind][reason] = by_reason[m.kind].get(reason, 0) + 1
    return {"turns": len(turns), "turns_with_misses": len({m.trace_id for m in records}),
            "misses": len(records), "by_kind": by_kind, "by_reason": by_reason}
