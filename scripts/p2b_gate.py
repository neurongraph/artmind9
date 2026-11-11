"""P2b gate measures (spec 2026-11-11 §7) from the registry's trace tables. Counts only, never question text.

uv run python scripts/p2b_gate.py <vault> <label>[@<offset>]=<run8>[,<run8>...] [...] [--groups 1-14,15-29]

@<offset> is added to benchmark_seq: a run of a partial question file (run L: Q15-Q29 only, seq 1 = Q15) is L@14.
"""
import json
import sqlite3
import statistics
import sys
from pathlib import Path

PARTIAL_CLOSERS = ("query page", "query survey", "query topics run")


def _args(text):
    try:
        value = json.loads(text or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}  # a span without params stores its argv list


def measure(db, run_prefixes, lo, hi, offset=0):
    turns = [r for p in run_prefixes for r in db.execute(
        "select trace_id, benchmark_seq from trace_turns where benchmark_run_id like ? and benchmark_seq between ? and ?",
        (p + "%", lo - offset, hi - offset))]
    per_turn, partial, followed, surveyed = [], 0, 0, 0
    guide_seen, guide_read = {}, {}
    for trace_id, _seq in turns:
        spans = db.execute("select command, args, rows, rows_total, truncated, evidence from trace_spans "
                           "where trace_id = ? order by started_at", (trace_id,)).fetchall()
        spans = [s for s in spans if (s[0] or "").startswith(("query", "db"))]
        per_turn.append(len(spans))
        surveyed += any(s[0] in ("query survey", "query topics run") for s in spans)
        # only the agent's Read tool calls: a result's warnings and the loaded skill also name reference paths
        events = " ".join(p or "" for (p,) in db.execute(
            "select payload from trace_events where trace_id = ? and name = 'Read'", (trace_id,)))
        for i, (cmd, args, rows, total, trunc, ev) in enumerate(spans):
            if trunc or (isinstance(total, int) and isinstance(rows, int) and total > rows):
                partial += 1
                later = spans[i + 1:]
                if any(c in PARTIAL_CLOSERS or _args(a).get("all") is True or _args(a).get("offset")
                       for c, a, *_ in later):
                    followed += 1
            for key in (_args(ev).get("guide") or []):
                guide_seen[key] = guide_seen.get(key, 0) + 1
                if f"references/{key}.md" in events:
                    guide_read[key] = guide_read.get(key, 0) + 1
    return {"turns": len(turns), "median_commands": statistics.median(per_turn) if per_turn else None,
            "partial": partial, "partial_followed": followed,
            "survey_turns": surveyed, "guide_seen": guide_seen, "guide_read": guide_read}


def main(argv):
    vault, rest = Path(argv[0]).expanduser(), argv[1:]
    groups = [(1, 999)]
    if "--groups" in rest:
        i = rest.index("--groups")
        groups = [tuple(int(x) for x in g.split("-")) for g in rest[i + 1].split(",")]
        rest = rest[:i] + rest[i + 2:]
    db = sqlite3.connect(vault / ".artmind" / "data" / "document_registry.db")
    for spec in rest:
        label, ids = spec.split("=", 1)
        label, _, off = label.partition("@")
        for lo, hi in groups:
            print(json.dumps({"label": label, "group": f"{lo}-{hi}", **measure(db, ids.split(","), lo, hi, int(off or 0))}))


if __name__ == "__main__":
    main(sys.argv[1:])
