"""Miss records (spec 2026-11-08 §4.1): what knowledge a turn was missing, derived from stored traces.

One record per miss, kinds: vocabulary, reformulation, identity, scope, structure, answer, user-stated. Pure
detectors over span dicts (as `store.list_spans` returns them); `load_and_derive` reads the registry. No LLM call,
no graph. Records hold private corpus text: write them only under `<ARTMIND_HOME>/logs/misses/`, never to stdout.
"""

from __future__ import annotations

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
