"""Miss records (spec 2026-11-08 §4.1): what knowledge a turn was missing, derived from stored traces.

One record per miss, kinds: vocabulary, reformulation, identity, scope, structure, answer, user-stated. Pure
detectors over span dicts (as `store.list_spans` returns them); `load_and_derive` reads the registry. No LLM call,
no graph. Records hold private corpus text: write them only under `<ARTMIND_HOME>/logs/misses/`, never to stdout.
"""

from __future__ import annotations

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


def _span_documents(span: dict) -> list[tuple[str | None, str | None]]:
    """(path, title) pairs a span returned: evidence documents plus provenance documents."""
    ev = span.get("evidence") or {}
    out = [(d.get("path"), None) for d in ev.get("documents") or [] if isinstance(d, dict)]
    prov = ev.get("provenance") if isinstance(ev.get("provenance"), dict) else {}
    for d in prov.get("documents") or []:
        if isinstance(d, dict):
            out.append((d.get("source_path") or d.get("path"), d.get("title")))
    return out


def _returned(span: dict, cited: str) -> bool:
    for held, title in _span_documents(span):
        if isinstance(held, str) and held and path_mod._same_document(cited, held):
            return True
        if isinstance(title, str) and title and title.casefold() == cited.casefold():
            return True
    return False


def cited_spans(answer: str | None, spans: list[dict]) -> dict[str, str | None]:
    """{cited source: span_id of the first span (by start time) that returned it, or None}."""
    ordered = sorted(spans, key=lambda s: s.get("started_at") or 0.0)
    links: dict[str, str | None] = {}
    for cited in path_mod.parse_sources(answer):
        links[cited] = next((s.get("span_id") for s in ordered if _returned(s, cited)), None)
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
