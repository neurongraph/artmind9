"""Miss records (spec 2026-11-08 §4.1): what knowledge a turn was missing, derived from stored traces.

One record per miss, kinds: vocabulary, reformulation, identity, scope, structure, answer, user-stated. Pure
detectors over span dicts (as `store.list_spans` returns them); `load_and_derive` reads the registry. No LLM call,
no graph. Records hold private corpus text: write them only under `<ARTMIND_HOME>/logs/misses/`, never to stdout.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

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
