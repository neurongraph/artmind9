"""A new review dossier pre-filled from a recorded trace (spec 2026-10-11
§7.2 step 1, §8.2). What the trace knows is written out; what needs
judgement is left as a placeholder comment for the reviewer."""

import re
from datetime import date, datetime

from artmind.reviews.dossier import PRIVACY_PREAMBLE, SECTIONS, Dossier, DossierError, iter_dossiers, new_review_id

ANSWER_EXCERPT = 1500
_SKIP_ARG_KEYS = frozenset({"domain", "compact", "render", "mode", "format", "as_of", "top_k"})
_HEXISH = re.compile(r"^[0-9a-f-]{16,}$", re.I)
_ACTIVE = ("open", "in-progress")

_PLACEHOLDERS = {
    "Diagnosis": "<!-- Stage verdict (corpus-gap | synthesis | routing | missing-view | identity | extraction | "
                 "schema; set `stage:` in the frontmatter to match) and the evidence chain: span id -> rows -> "
                 "chunk sentence -> staging entry. Quote source sentences as > blockquotes. "
                 "See the artmind-review skill, references/stage-walk.md. -->",
    "Shape audit": "<!-- Required for identity / extraction / schema verdicts. A table | element | check | "
                   "source sentence | why the question needs it |, then the answerability pattern as JSON. -->",
    "Proposal": "<!-- One of: view.yaml + SKILL.md draft | schema YAML diff | meta.yaml prompt diff | code-change "
                "spec (files, functions, behaviour) | for corpus-gap: what to ingest. -->",
    "Proof": "<!-- `artmind views test` output for a view; a re-extraction shape diff once P3 ships. -->",
    "Synthetic example": "<!-- The same failure with invented entities and text, for tests in the public checkout. -->",
}


def _quote(text: str) -> str:
    return "\n".join(f"> {line}" if line else ">" for line in text.strip().split("\n"))


def _spans(shown: dict) -> list[dict]:
    spans = [s for e in shown["events"] for s in e.get("spans", [])]
    return spans + list(shown.get("unattributed_spans") or [])


def harvest_private_terms(shown: dict) -> list[str]:
    terms: dict[str, None] = {}

    def add(value) -> None:
        if isinstance(value, str):
            value = value.strip()
            if 3 <= len(value) <= 60 and not value.startswith("-") and not _HEXISH.match(value):
                terms.setdefault(value, None)

    for span in _spans(shown):
        evidence = span.get("evidence") or {}
        for candidate in evidence.get("resolved") or []:
            add(candidate.get("name"))
        for param in ((evidence.get("view") or {}).get("params") or {}).values():
            if isinstance(param, dict):
                add(param.get("input"))
                add(param.get("name"))
            else:
                add(param)
        args = span.get("args")
        if isinstance(args, dict):
            for key, value in args.items():
                if key in _SKIP_ARG_KEYS:
                    continue
                for item in value if isinstance(value, (list, tuple)) else [value]:
                    add(item)
    return list(terms)


def _symptom(shown: dict) -> str:
    turn = shown["turn"]
    feedback = shown.get("feedback") or {}
    state = turn.get("graph_state") or {}
    started = datetime.fromtimestamp(turn["started_at"]).isoformat(timespec="minutes")
    cost = f" · ${turn['cost_usd']:.2f}" if turn.get("cost_usd") is not None else ""
    rating = feedback.get("rating") or "unrated"
    if feedback.get("expected"):
        rating += f" — expected: {feedback['expected']}"
    lines = [
        f"**Trace:** `{turn['trace_id']}` · {turn['source']} · {started} · {turn.get('turns') or '?'} turns{cost}",
        f"**Rating:** {rating}",
        f"**Graph state:** artmind {state.get('artmind_version')}, vault HEAD `{(state.get('vault_head') or '')[:12]}`",
        "",
        "**Question**",
        "",
        _quote(turn["question"]),
        "",
        "**Answer (excerpt)**",
        "",
        _quote((turn.get("answer") or "(no answer)")[:ANSWER_EXCERPT]),
        "",
        f"**Retrieval path** — fell through: {'yes' if turn.get('fell_through') else 'no'}, "
        f"answer basis: `{turn.get('answer_basis')}`, graph attempts {turn.get('graph_attempts') or 0} "
        f"({turn.get('graph_productive') or 0} returned rows)",
        "",
        "| # | layer | command | rows |",
        "|---|---|---|---|",
    ]
    for i, step in enumerate(turn.get("retrieval_path") or [], 1):
        lines.append(f"| {i} | {step['layer']} | `{step['command']}` | {step['rows']} |")
    return "\n".join(lines)


def scaffold(trace_id: str, *, stage: str | None = None, elements: list[str] | tuple = (),
             today: date | None = None) -> Dossier:
    from artmind.traces.analysis import show

    shown = show(trace_id)
    if shown is None:
        raise DossierError(f"no trace {trace_id}")
    turn = shown["turn"]
    review_id = new_review_id(today)
    day = (today or date.today()).isoformat()
    meta = {
        "review_id": review_id,
        "status": "open",
        "stage": stage,
        "elements": list(elements),
        "trace_ids": [trace_id],
        "private_terms": harvest_private_terms(shown),
        "graph_state": turn.get("graph_state") or {},
        "created": day,
        "updated": day,
    }
    sections = {title: _PLACEHOLDERS.get(title, "") for title in SECTIONS}
    sections["Symptom"] = _symptom(shown)
    sections["Regression question"] = (
        f"### R-{review_id} — <!-- short title -->\n\n**Question:** {turn['question'].strip()}\n\n"
        "**Evaluation comment:** <!-- what a correct answer must say; for a graph miss also: answered from "
        "the graph (answer_basis graph or mixed, no fall-through) -->"
    )
    sections["Agent prompt"] = (
        f"{PRIVACY_PREAMBLE}\n\n<!-- A self-contained task for a coding session: context, files, acceptance "
        f"criteria (tests built on the Synthetic example; `artmind review leakcheck {review_id} --base master` "
        "passes; the regression question passes, after a re-ingest if the schema changed). -->"
    )
    sections["Log"] = f"- {day} opened from trace `{trace_id}`"
    return Dossier(meta=meta, sections=sections)


def open_reviews_for(trace_id: str) -> list[str]:
    """Review ids of open or in-progress dossiers that cover `trace_id` (from the files)."""
    return [d.review_id for _, d, err in iter_dossiers()
            if err is None and d.meta.get("status") in _ACTIVE and trace_id in (d.meta.get("trace_ids") or [])]
