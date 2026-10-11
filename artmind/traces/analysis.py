"""The read side behind `artmind traces` (spec 2026-10-11 §4.1, §6):
triage ranking, fall-through stats, and one turn's evidence chain."""

import re
import statistics
import time
from collections import Counter
from datetime import datetime, timedelta

from artmind.traces import store
from artmind.traces.path import GRAPH_LAYERS, is_graph_miss, layer_of

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


def signals(turn: dict, spans: list[dict], turns_p90: float | None) -> dict[str, int]:
    out: dict[str, int] = {}
    if is_graph_miss(turn):
        unproductive = (turn.get("graph_attempts") or 0) - (turn.get("graph_productive") or 0)
        out["graph_miss"] = 3 + min(3, max(0, unproductive))
    elif turn.get("fell_through"):
        out["fell_through"] = 1
    if any(s.get("exit_code") == 0 and (s.get("rows") or 0) == 0 for s in spans):
        out["empty_rows"] = 1
    if any(s.get("truncated") for s in spans):
        out["truncated"] = 1
    if any(s.get("command") == "query graph text2cypher" for s in spans):
        out["text2cypher"] = 1
    if len({s.get("command") for s in spans if layer_of(s.get("command")) != "discovery"}) >= 3:
        out["deep_fallback"] = 1
    if turns_p90 is not None and (turn.get("turns") or 0) > turns_p90:
        out["many_turns"] = 1
    if turn.get("answer") and _NOT_FOUND.search(turn["answer"]):
        out["not_found_answer"] = 1
    if any(_ambiguous(s) for s in spans):
        out["ambiguous_entity"] = 1
    if any(s.get("exit_code") not in (0, None) for s in spans):
        out["command_error"] = 1
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
    items = []
    for turn in turns:
        rating = turn.get("rating")
        sig = signals(turn, spans_by_trace.get(turn["trace_id"], []), p90)
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


def stats(*, domain: list[str] | None = None, since: float | None = None, by: str = "week") -> list[dict]:
    buckets: dict[tuple[str, str], list[dict]] = {}
    for turn in store.list_turns(since=since):
        if turn["status"] != "done":
            continue
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
        "events": events,
        "unattributed_spans": [s for s in spans if not s["tool_id"] or s["tool_id"] not in tool_call_ids],
        "graph_layers": sorted(GRAPH_LAYERS),
        "warnings": warnings,
    }
