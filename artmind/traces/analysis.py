"""The read side behind `artmind traces` (spec 2026-10-11 §4.1, §6):
triage ranking, fall-through stats, and one turn's evidence chain."""

import json
import re
import shlex
import statistics
import time
from collections import Counter
from datetime import datetime, timedelta

from artmind.traces import store
from artmind.traces.path import GRAPH_LAYERS, is_dry_run, is_graph_miss, span_layer, views_check

_NOT_FOUND = re.compile(
    r"\b(couldn'?t find|could not find|unable to find|no information|not in the (graph|knowledge graph|data)"
    r"|(?:the (?:graph|data|knowledge graph|documents?) )does(?: not|n'?t) (?:contain|answer|say)"
    r"|no (matching|relevant) (data|results|records|entities))\b",
    re.IGNORECASE,
)
_RELATIVE = re.compile(r"^(\d+)([dh])$")


def parse_since(value: str | None, *, now: float | None = None) -> float | None:
    """`7d`, `24h` or an ISO date/datetime -> epoch seconds."""
    if not value:
        return None
    now = time.time() if now is None else now
    match = _RELATIVE.match(value.strip())
    if match:
        amount, unit = int(match.group(1)), match.group(2)
        return now - amount * (86400 if unit == "d" else 3600)
    try:
        return datetime.fromisoformat(value.strip()).timestamp()
    except ValueError:
        raise ValueError(f"--since must look like 7d, 24h or 2026-10-01, not {value!r}") from None


def _ambiguous(span: dict) -> bool:
    resolved = (span.get("evidence") or {}).get("resolved") or []
    scores = [c.get("score") for c in resolved[:2]]
    return (len(scores) == 2 and all(isinstance(s, (int, float)) for s in scores)
            and scores[0] > 0 and scores[1] >= 0.9 * scores[0])


def views_checks(turns: list[dict], spans_by_trace: dict[str, list[dict]]) -> dict[str, dict]:
    """`path.views_check` per turn, with listings from earlier turns of the same
    conversation carried forward: the skill lists views once per conversation.
    Earlier turns outside `turns` (before a `--since` window) are fetched too."""
    sessions = {t["session_id"] for t in turns if t.get("session_id")}
    history = [t for t in store.list_turns() if t.get("session_id") in sessions] if sessions else []
    known = {t["trace_id"] for t in turns}
    extra = [t["trace_id"] for t in history if t["trace_id"] not in known]
    spans = {**spans_by_trace, **store.list_spans_for(extra)} if extra else spans_by_trace
    carried: dict[str, tuple[set[str], bool]] = {}
    out: dict[str, dict] = {}
    by_id = {t["trace_id"]: t for t in [*history, *turns]}
    for turn in sorted(by_id.values(), key=lambda t: t["started_at"]):
        sid = turn.get("session_id")
        listed, listed_all = carried.get(sid, (set(), False)) if sid else (set(), False)
        check = views_check(spans.get(turn["trace_id"], []), listed, listed_all_before=listed_all)
        if sid:
            carried[sid] = (set(check["listed"]), check["listed_all"])
        out[turn["trace_id"]] = check
    return out


def shadow_plan(events: list[dict]) -> dict | None:
    """The turn's `plan_shadow` payload (spec 2026-10-22 §6), or None. A payload
    cut by store.cap is no longer JSON: reported as `truncated`."""
    for event in events:
        if event.get("type") == "plan_shadow":
            try:
                value = json.loads(event.get("payload") or "null")
            except ValueError:
                return {"status": "truncated", "followup": None, "plan": None, "error": None}
            return value if isinstance(value, dict) else None
    return None


def _command_key(cmd: str) -> str | None:
    """`artmind query views run v --domain b` -> `query views run v` (words before the first option)."""
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return None
    if not tokens or tokens[0] != "artmind":
        return None
    words = []
    for token in tokens[1:]:
        if token.startswith("-"):
            break
        words.append(token)
    return " ".join(words) or None


def _span_key(span: dict) -> str:
    command = span.get("command") or ""
    args = span.get("args")
    evidence = span.get("evidence")
    if command == "query plan" and isinstance(evidence, dict) and evidence.get("view"):
        return f"query views run {evidence['view']}"  # --execute ran the view
    if command == "query views run" and isinstance(args, dict) and args.get("name"):
        return f"{command} {args['name']}"
    return command


def _is_traced_step(key: str | None) -> bool:
    return key is not None and (key == "query" or key.startswith("query "))


def agreement(shadow: dict | None, spans: list[dict]) -> bool | None:
    """Did the agent run the shadow plan's first traced step? Only `query ...`
    steps leave spans (an `artmind db ...` step is skipped); None when the plan
    has none. A bare `query plan` span is the plan itself, never an answer."""
    plan = (shadow or {}).get("plan") or {}
    steps = plan.get("steps") or plan.get("suggested_steps") or []
    keys = (_command_key(step.get("cmd") or "") for step in steps)
    key = next((k for k in keys if _is_traced_step(k)), None)
    if key is None:
        return None
    return key in {_span_key(s) for s in spans if _span_key(s) != "query plan"}


def _rate(values: list) -> float | None:
    known = [v for v in values if v is not None]
    return sum(1 for v in known if v) / len(known) if known else None


def _shadow_rates(turns: list[dict], shadows: dict[str, list[dict]], spans_by_trace: dict[str, list[dict]]) -> dict:
    """Shadow-plan block of a stats row; always the same keys. `statuses` counts
    every recorded payload (a failing shadow must be visible); the rates use
    status `ok` only, and the first/followup/by-rating splits use high plans."""
    statuses: Counter = Counter()
    rows = []
    for turn in turns:
        payload = shadow_plan(shadows.get(turn["trace_id"], []))
        if not payload:
            continue
        statuses[payload.get("status") or "unknown"] += 1
        if payload.get("status") == "ok":
            level = ((payload.get("plan") or {}).get("confidence") or {}).get("level")
            rows.append((turn, payload, level, agreement(payload, spans_by_trace.get(turn["trace_id"], []))))
    high = [r for r in rows if r[2] == "high"]
    by_rating: dict[str, list] = {}
    for turn, _, _, agreed in high:
        by_rating.setdefault(turn.get("rating") or "unrated", []).append(agreed)
    return {
        "plans": len(rows),
        "statuses": dict(statuses),
        "high_share": len(high) / len(rows) if rows else None,
        "agreement_high": _rate([a for *_, lvl, a in rows if lvl == "high"]),
        "agreement_low": _rate([a for *_, lvl, a in rows if lvl != "high"]),
        "agreement_first": _rate([a for _, p, _, a in high if not p.get("followup")]),
        "agreement_followup": _rate([a for _, p, _, a in high if p.get("followup")]),
        "agreement_by_rating": {k: _rate(v) for k, v in sorted(by_rating.items())},
    }


def _is_unexecuted_plan(span: dict) -> bool:
    """A `query plan` that did not run a view: its zero rows are not a failed retrieval."""
    evidence = span.get("evidence")
    return span.get("command") == "query plan" and not (isinstance(evidence, dict) and evidence.get("view"))


def signals(turn: dict, spans: list[dict], turns_p90: float | None,
            views: dict | None = None, shadow: dict | None = None) -> dict[str, int]:
    """`views` is the turn's `views_checks` entry; computed from `spans`
    alone (no earlier turns) when not given."""
    out: dict[str, int] = {}
    if is_graph_miss(turn):
        unproductive = (turn.get("graph_attempts") or 0) - (turn.get("graph_productive") or 0)
        out["graph_miss"] = 3 + min(3, max(0, unproductive))
    elif turn.get("fell_through"):
        out["fell_through"] = 1
    # An empty `views list` is a domain with no views, not a failed retrieval.
    if any(s.get("exit_code") == 0 and (s.get("rows") or 0) == 0 and s.get("command") != "query views list"
           and not is_dry_run(s) and not _is_unexecuted_plan(s) for s in spans):
        out["empty_rows"] = 1
    if any(s.get("truncated") for s in spans):
        out["truncated"] = 1
    if any(s.get("command") == "query graph text2cypher" for s in spans):
        out["text2cypher"] = 1
    if len({s.get("command") for s in spans if span_layer(s) not in ("discovery", "plan")}) >= 3:
        out["deep_fallback"] = 1
    if turns_p90 is not None and (turn.get("turns") or 0) > turns_p90:
        out["many_turns"] = 1
    if turn.get("answer") and _NOT_FOUND.search(turn["answer"]):
        out["not_found_answer"] = 1
    if any(_ambiguous(s) for s in spans):
        out["ambiguous_entity"] = 1
    if any(s.get("exit_code") not in (0, None) for s in spans):
        out["command_error"] = 1
    status = (views or views_check(spans))["status"]
    if status == "skipped":
        out["views_skipped"] = 1
    elif status == "late":
        out["views_late"] = 1
    level = (((shadow or {}).get("plan") or {}).get("confidence") or {}).get("level")
    ran_query = any((s.get("command") or "").startswith("query") and s.get("command") != "query plan" for s in spans)
    if (shadow or {}).get("status") == "ok" and level == "high" and ran_query and agreement(shadow, spans) is False:
        out["plan_disagreed_high"] = 2
    return out


def _p90(values: list[int]) -> float | None:
    if len(values) < 10:
        return None  # too few turns for a percentile to mean anything
    return statistics.quantiles(values, n=10)[-1]


def triage(*, since: float | None = None, limit: int = 20) -> list[dict]:
    """Turns worth reviewing: rated wrong, then partial, then unrated with
    signals, with `good`-rated graph misses after unrated turns of equal score."""
    turns = [t for t in store.list_turns(since=since) if t["status"] in ("done", "error") and not t["reviewed"]]
    p90 = _p90([t["turns"] or 0 for t in turns])
    spans_by_trace = store.list_spans_for([t["trace_id"] for t in turns])
    checks = views_checks(turns, spans_by_trace)
    shadows = store.list_events_of_type([t["trace_id"] for t in turns], "plan_shadow")
    items = []
    for turn in turns:
        rating = turn.get("rating")
        sig = signals(turn, spans_by_trace.get(turn["trace_id"], []), p90, checks[turn["trace_id"]],
                      shadow=shadow_plan(shadows.get(turn["trace_id"], [])))
        score = sum(sig.values())
        if rating == "good" and "graph_miss" not in sig:
            continue
        if rating is None and score == 0:
            continue
        tier = {"wrong": 0, "partial": 1}.get(rating, 2)
        items.append({
            "trace_id": turn["trace_id"], "rating": rating, "expected": turn.get("expected"),
            "score": score, "signals": sig, "question": turn["question"], "started_at": turn["started_at"],
            "_key": (tier, -score, 1 if rating == "good" else 0, -turn["started_at"]),
        })
    items.sort(key=lambda item: item["_key"])
    for item in items:
        del item["_key"]
    return items[:limit]


def _period(started_at: float, by: str) -> str:
    day = datetime.fromtimestamp(started_at).date()
    if by == "day":
        return day.isoformat()
    if by == "week":
        return (day - timedelta(days=day.weekday())).isoformat()
    return "all"


def _views_rates(turns: list[dict], checks: dict[str, dict]) -> dict:
    """Views-first compliance over the turns that ran any query besides Route.
    `views_listed_unused` counts turns that listed views, found some, and ran
    none: a possible miss, or a correct "no view fits"."""
    cs = [checks[t["trace_id"]] for t in turns]
    applicable = [c for c in cs if c["status"] != "n/a"]
    n = len(applicable)

    def rate(status: str) -> float | None:
        return sum(1 for c in applicable if c["status"] == status) / n if n else None

    return {
        "views_first_rate": rate("first"),
        "views_late_rate": rate("late"),
        "views_skipped_rate": rate("skipped"),
        "view_run_rate": sum(1 for c in cs if c["view_ran"]) / len(cs) if cs else None,
        "views_listed_unused": sum(1 for c in cs if (c["views_available"] or 0) > 0 and not c["view_ran"]),
    }


def stats(*, domain: list[str] | None = None, since: float | None = None, by: str = "week") -> list[dict]:
    buckets: dict[tuple[str, str], list[dict]] = {}
    done = [t for t in store.list_turns(since=since) if t["status"] == "done"]
    ids = [t["trace_id"] for t in done]
    spans_by_trace = store.list_spans_for(ids)
    checks = views_checks(done, spans_by_trace)
    shadows = store.list_events_of_type(ids, "plan_shadow")
    for turn in done:
        for d in turn.get("domains") or ["(none)"]:
            if domain and d not in domain:
                continue
            buckets.setdefault((d, _period(turn["started_at"], by)), []).append(turn)
    rows = []
    for (d, period), turns in sorted(buckets.items()):
        n = len(turns)
        attempts = sum(t["graph_attempts"] or 0 for t in turns)
        fell = [t for t in turns if t["fell_through"]]
        rows.append({
            "domain": d, "period": period, "turns": n,
            "fall_through_rate": len(fell) / n,
            "graph_miss_rate": sum(1 for t in turns if is_graph_miss(t)) / n,
            "graph_productivity": (sum(t["graph_productive"] or 0 for t in turns) / attempts) if attempts else None,
            "answer_basis": dict(Counter(t["answer_basis"] or "none" for t in turns)),
            "mean_graph_attempts_before_fall_through":
                statistics.mean(t["graph_attempts"] or 0 for t in fell) if fell else None,
            **_views_rates(turns, checks),
            "plan_shadow": _shadow_rates(turns, shadows, spans_by_trace),
        })
    return rows


def show(trace_id: str) -> dict | None:
    turn = store.get_turn(trace_id)
    if turn is None:
        return None
    events = store.list_events(trace_id)
    spans = store.list_spans(trace_id)
    calls = store.list_llm_calls([s["span_id"] for s in spans])
    for span in spans:
        span["llm_calls"] = calls.get(span["span_id"], [])
    by_tool: dict[str, list[dict]] = {}
    for span in spans:
        if span["tool_id"]:
            by_tool.setdefault(span["tool_id"], []).append(span)
    for event in events:
        if event["type"] == "tool_call":
            event["spans"] = by_tool.get(event["tool_id"], [])
    warnings = []
    ran_query = any(e["type"] == "tool_call" and "artmind query" in (e["payload"] or "") for e in events)
    if ran_query and not spans:
        warnings.append(
            "No CLI spans were recorded although the agent ran `artmind query`. A `serve` daemon started "
            "before trace capture shipped answers without recording: restart it (just dev-stop-daemons, "
            "then `artmind serve` from the vault)."
        )
    tool_call_ids = {e["tool_id"] for e in events if e["type"] == "tool_call"}
    return {
        "turn": turn,
        "feedback": store.latest_feedback(trace_id),
        "plan_shadow": shadow_plan(events),
        "events": [e for e in events if e["type"] != "plan_shadow"],
        "unattributed_spans": [s for s in spans if not s["tool_id"] or s["tool_id"] not in tool_call_ids],
        "graph_layers": sorted(GRAPH_LAYERS),
        "warnings": warnings,
    }
