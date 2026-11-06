"""Document reach of a plan's own steps (B2 spec 2026-11-07 §8.3): `artmind plan replay --reach`.

For each label with `expected_documents`, the first plan's read-only steps (`steps` + `suggested_steps`,
every child of a `par` counted) run in process, and a document is *reached* when it appears in any
step's evidence. The needle-or-merge rule (§4.2) is applied: when the program's `pick` node has a
`when` over `needle.*` facts and the predicate is false, the needle answers alone and only its documents
(plus those of steps outside that `par`) count. `reached_all` counts every step.

Only `artmind query ...` commands run; anything else (`artmind db ...`, a shell word) is skipped and
recorded. A step that errors is recorded and the reach goes on.

Document fields read from step payloads (see `step_documents`): dicts under the keys `document`
(vector-text full row), `doc` (brief row), `documents` and `source_documents` (survey rows, graph rows,
entity-context, document-history), and any dict carrying `doc_id` (survey rows). From each such dict:
`path`, `source_path`, `name`, `id` / `doc_id`, `title`.

Counts and label ids only go to the printed report; matched and missed paths go to the private logs.
"""

from __future__ import annotations

import json
import posixpath
import shlex
from datetime import datetime
from pathlib import Path

from artmind.query_plan import program as program_mod

DOC_KEYS = ("document", "doc", "documents", "source_documents")


# ── documents in a payload ──────────────────────────────────────────────────

def step_documents(payload) -> list[dict]:
    """Every document dict in a step payload: values under `DOC_KEYS` (a dict or a list of dicts) and any
    dict with a `doc_id`, at any depth. Each is normalised to {path, source_path, name, id, title}."""
    found: list[dict] = []

    def add(d: dict) -> None:
        ref = {"path": d.get("path"), "source_path": d.get("source_path"), "name": d.get("name"),
               "id": d.get("id") if "id" in d else d.get("doc_id"), "title": d.get("title")}
        if any(v for v in ref.values()):
            found.append(ref)

    def walk(node, key=None) -> None:
        if isinstance(node, dict):
            if key in DOC_KEYS or "doc_id" in node:
                add(node)
            for k, v in node.items():
                walk(v, k)
        elif isinstance(node, list):
            for v in node:
                walk(v, key)

    walk(payload)
    return found


# ── matching ───────────────────────────────────────────────────────────────

def _norm(value) -> str:
    text = str(value or "").replace("\\", "/").strip().casefold()
    while text.startswith("./"):
        text = text[2:]
    return text.lstrip("/")


def _stem(name: str) -> str:
    return posixpath.splitext(posixpath.basename(name))[0]


def match_kind(expected: str, doc: dict) -> str | None:
    """How `doc` matches the expected entry: "path" (equal, or one a trailing path of the other, in
    `path` / `source_path` / `name` / `id`), "basename" (same file name, case-insensitive), "title"
    (the title equals the file name without its extension; brief rows carry only that), or None."""
    want = _norm(expected)
    if not want:
        return None
    paths = [_norm(doc.get(k)) for k in ("path", "source_path", "name", "id") if doc.get(k)]
    for have in paths:
        if have == want or have.endswith("/" + want) or (have.count("/") and want.endswith("/" + have)):
            return "path"
    base = posixpath.basename(want)
    if any(posixpath.basename(have) == base for have in paths):
        return "basename"
    title = _norm(doc.get("title"))
    if title and title in (_stem(want), base):
        return "title"
    return None


def reached_documents(expected, docs: list[dict]) -> tuple[list[str], list[str]]:
    """(matched, missed) of the expected entries against the step documents."""
    matched, missed = [], []
    for want in expected:
        (matched if any(match_kind(want, d) for d in docs) else missed).append(want)
    return matched, missed


# ── the needle-or-merge rule ───────────────────────────────────────────────

def _walk_program(nodes):
    for n in nodes or []:
        yield n
        yield from _walk_program(n.get("children"))


def needle_rule(plan: dict) -> dict | None:
    """The plan's pick-with-`when` over the needle, or None. {"needle": cmd, "others": [cmds of the other
    children of the pick's source], "when": Predicate}."""
    program = plan.get("program") or []
    needle = next((n for n in _walk_program(program) if n.get("id") == "needle" and n.get("run")), None)
    if needle is None:
        return None
    for node in _walk_program(program):
        if node.get("kind") != "pick" or not node.get("when"):
            continue
        try:
            pred = program_mod.parse_predicate(node["when"], "reach")
        except program_mod.ProgramError:
            return None
        if pred.nodes() - {"needle"}:
            return None
        source = next((n for n in _walk_program(program) if n.get("id") == (node.get("pick") or {}).get("from")), None)
        others = [c["run"] for c in (source or {}).get("children") or [] if c.get("run") and c is not needle]
        return {"needle": needle["run"], "others": others, "when": pred}
    return None


def needle_answers_alone(rule: dict, needle_payload: dict | None) -> bool:
    """False (the merge runs) unless the needle ran and the `when` over its facts is false. A missing
    score_drop (no clear winner) reads as minus infinity, so `score_drop < NEEDLE_MARGIN` holds."""
    if not isinstance(needle_payload, dict):
        return False
    from artmind.retrieval_eval import metrics

    facts = metrics.needle_stats(needle_payload.get("rows") or [])
    if facts["score_drop"] is None:
        facts["score_drop"] = float("-inf")
    facts.update(program_mod.CONSTANTS)
    return not program_mod.evaluate_plan_predicate(rule["when"], facts)


# ── running steps ───────────────────────────────────────────────────────────

def runnable(cmd: str) -> list[str] | None:
    """The Click args of an `artmind query ...` command, else None (skipped)."""
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return None
    if len(tokens) < 3 or tokens[0] != "artmind" or tokens[1] != "query":
        return None
    return tokens[1:]


def run_step_in_process(args: list[str]) -> dict:
    """Invoke the Click command in process, in-process (no `serve` proxy), output budget unset so the
    payload is whole."""
    import os

    from click.testing import CliRunner

    from artmind.cli import cli

    saved = os.environ.pop("ARTMIND_OUTPUT_BUDGET", None)
    try:
        result = CliRunner().invoke(cli, args)
    finally:
        if saved is not None:
            os.environ["ARTMIND_OUTPUT_BUDGET"] = saved
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise result.exception
    if result.exit_code != 0:
        raise RuntimeError(f"exit {result.exit_code}")
    return json.loads(result.output)


def plan_steps(plan: dict) -> list[str]:
    return [s["cmd"] for s in [*(plan.get("steps") or []), *(plan.get("suggested_steps") or [])] if s.get("cmd")]


def reach_label(label, plan: dict, step_fn=run_step_in_process) -> dict:
    """Run the plan's steps and measure the reach of `label.expected_documents`. `detail` holds paths and
    error text (private); the rest is counts."""
    expected = list(label.expected_documents)
    rule = needle_rule(plan)
    per_step: list[dict] = []
    payloads: dict[str, dict] = {}
    for cmd in plan_steps(plan):
        args = runnable(cmd)
        if args is None:
            per_step.append({"cmd": cmd, "status": "skipped"})
            continue
        try:
            payloads[cmd] = step_fn(args)
            per_step.append({"cmd": cmd, "status": "ok"})
        except Exception as exc:  # recorded, never fatal
            per_step.append({"cmd": cmd, "status": "error", "error_type": type(exc).__name__, "error": str(exc)})
    alone = bool(rule) and needle_answers_alone(rule, payloads.get(rule["needle"]))
    excluded = set(rule["others"]) if alone else set()

    def docs_of(skip: set[str]) -> list[dict]:
        return [d for cmd, p in payloads.items() if cmd not in skip for d in step_documents(p)]

    matched, missed = reached_documents(expected, docs_of(excluded))
    matched_all, _ = reached_documents(expected, docs_of(set()))
    count = lambda status: sum(1 for s in per_step if s["status"] == status)  # noqa: E731
    return {"id": label.id, "expected": len(expected), "reached": len(matched), "reached_all": len(matched_all),
            "missed": len(missed), "steps_run": count("ok"), "steps_skipped": count("skipped"),
            "steps_errored": count("error"), "needle_alone": alone,
            "detail": {"matched": matched, "missed": missed, "steps": per_step}}


def totals(rows: list[dict]) -> dict:
    return {"labels": len(rows), "expected": sum(r["expected"] for r in rows),
            "reached": sum(r["reached"] for r in rows), "reached_all": sum(r["reached_all"] for r in rows),
            "needle_alone": sum(1 for r in rows if r["needle_alone"]),
            "steps_errored": sum(r["steps_errored"] for r in rows),
            "steps_skipped": sum(r["steps_skipped"] for r in rows)}


def reach(labels, classifier: str, *, model: str | None = None, given_domains: bool = False,
          plan_fn=None, step_fn=run_step_in_process) -> dict:
    """Plan every label that has `expected_documents` once and measure its reach."""
    from artmind.query_plan import replay as replay_mod

    subset = [lb for lb in labels if lb.expected_documents]
    plans: dict[str, dict] = {}
    report = replay_mod.replay(subset, classifier, 1, given_domains=given_domains, model=model,
                               plan_fn=plan_fn, threads={}, plans_out=plans)
    errors = {r["id"]: r for r in report["errors"]}
    rows = []
    for lb in subset:
        if lb.id in errors:
            n = len(lb.expected_documents)
            rows.append({"id": lb.id, "expected": n, "reached": 0, "reached_all": 0, "missed": n,
                         "steps_run": 0, "steps_skipped": 0, "steps_errored": 0, "needle_alone": False,
                         "plan_error": errors[lb.id].get("error_type"),
                         "detail": {"matched": [], "missed": list(lb.expected_documents), "steps": []}})
            continue
        rows.append(reach_label(lb, plans[lb.id], step_fn))
    return {"classifier": classifier, "totals": totals(rows), "labels": rows}


def write_detail(report: dict) -> Path:
    """Per-label paths into <ARTMIND_HOME>/logs/ (gitignored, private)."""
    import paths

    path = Path(paths.LOGS_DIR) / f"plan-reach-{datetime.now().strftime('%Y%m%dT%H%M%S%f')}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    return path


def public_report(report: dict) -> dict:
    """The printable report: counts and label ids, no `detail`."""
    return {**report, "labels": [{k: v for k, v in r.items() if k != "detail"} for r in report["labels"]]}
