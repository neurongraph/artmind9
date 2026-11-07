"""`query run --each FILE` (P3 spec §8.6; planner v2 §7.5): a fixed program per claim, in-process.

Per item: needle vector-text (top 10) beside a brief survey (top 10), unioned; the claim's own
`source_doc` is left out of the evidence. Claims are used as written (the catalogue has no `terms` yet).
No LLM call. One bundle per item goes to <ARTMIND_HOME>/logs/runs/<handle>.jsonl (private, gitignored);
the command prints a summary only.
"""

from __future__ import annotations

import json
import secrets
import shlex
from dataclasses import dataclass
from pathlib import Path

from artmind.query_run import bundle as bundle_mod
from artmind.query_run import executor
from artmind.query_run.replay_run import p90

CLAIM_CHARS = 1000
MAX_ITEMS_DEFAULT = 50
ITEM_BUDGET = 12_000
_EXTENSIONS = frozenset({"md", "pdf", "docx", "pptx", "txt", "xlsx", "csv", "html", "doc", "ppt", "xls"})


class ClaimsError(ValueError):
    pass


@dataclass(frozen=True)
class Claim:
    id: str
    claim: str
    source_doc: str | None = None


def load_claims(path) -> list[Claim]:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ClaimsError(f"cannot read {path}: {exc}") from exc
    if path.suffix in (".yaml", ".yml"):
        import yaml

        try:
            raw = yaml.safe_load(text) or []
        except yaml.YAMLError as exc:
            raise ClaimsError(f"{path} is not valid YAML: {exc}") from exc
        if not isinstance(raw, list):
            raise ClaimsError(f"{path}: expected a list of {{id, claim, source_doc?}}")
        items = list(enumerate(raw, 1))
    else:
        items = []
        for n, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                items.append((n, json.loads(line)))
            except ValueError as exc:
                raise ClaimsError(f"{path} line {n}: not JSON ({exc})") from None
    out, seen = [], set()
    for n, item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"].strip():
            raise ClaimsError(f"{path} item {n}: needs a string `id`")
        if not isinstance(item.get("claim"), str) or not item["claim"].strip():
            raise ClaimsError(f"{path} item {n} ({item['id']}): needs a string `claim`")
        if item["id"] in seen:
            raise ClaimsError(f"{path}: duplicate id {item['id']!r}")
        seen.add(item["id"])
        src = item.get("source_doc")
        out.append(Claim(item["id"], item["claim"], src if isinstance(src, str) and src else None))
    return out


def item_plan(claim: Claim, domains: list[str]) -> dict:
    text = shlex.quote(claim.claim[:CLAIM_CHARS])
    dom = shlex.quote(",".join(domains))
    needle = f"artmind query vector-text --shape brief --domain {dom} --topK 10 {text} --compact"
    wide = f"artmind query survey --shape brief --domain {dom} --topDocs 10 {text} --compact"
    return {"program": [{"id": "p", "kind": "par", "children": [
                {"id": "needle", "kind": "run", "run": needle, "filled": True},
                {"id": "wide", "kind": "run", "run": wide, "filled": True}]},
                        {"id": "u", "kind": "pick", "pick": {"from": "p", "how": "union", "score": []}}],
            "guards": [], "replacements": {}, "route": {"id": "batch_claims"}, "program_kind": "batch_claims",
            "intent": {"id": "retrieve"}, "confidence": {"level": "low"},
            "scope": {"domains": list(domains), "excluded_documents": [claim.source_doc] if claim.source_doc else [],
                      "rules": []},
            "gaps": [], "suggested_steps": [], "fallbacks": []}


def _drop_source(printed: dict, source: str | None) -> dict:
    if not source:
        return printed
    def forms(name: str) -> set[str]:
        name = name.casefold().strip()
        stem, dot, ext = name.rpartition(".")
        return {name, stem} if dot and ext in _EXTENSIONS else {name}

    wanted = forms(source)

    def own(doc: dict) -> bool:
        return any(forms(str(doc.get(f) or "")) & wanted for f in ("id", "title", "doc_id") if doc.get(f))

    printed["evidence"] = [e for e in printed.get("evidence") or [] if not own(e.get("doc") or {})]
    printed["documents"] = [d for d in printed.get("documents") or [] if not own(d)]
    return printed


def run_each(claims: list[Claim], domains: list[str], *, step_fn=None, max_items: int = MAX_ITEMS_DEFAULT) -> dict:
    import paths

    runs_dir = Path(paths.LOGS_DIR) / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    handle = secrets.token_hex(6)
    out_path = runs_dir / f"{handle}.jsonl"
    took = claims[:max_items]
    times, escalated = [], 0
    with out_path.open("w", encoding="utf-8") as fh:
        for claim in took:
            plan = item_plan(claim, domains)
            ex = executor.execute(plan, step_fn=step_fn)
            printed, _ = bundle_mod.build(plan, ex, budget=ITEM_BUDGET)
            printed = _drop_source(printed, claim.source_doc)
            times.append(ex.exec_ms)
            escalated += bool(ex.escalate)
            fh.write(json.dumps({"id": claim.id, "bundle": printed}, ensure_ascii=False, default=str) + "\n")
    return {"items": len(took), "skipped_items": len(claims) - len(took), "escalated": escalated,
            "exec_ms_p50": sorted(times)[len(times) // 2] if times else None, "exec_ms_p90": p90(times),
            "handle": handle, "file": str(out_path)}
