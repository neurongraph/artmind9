"""Retrospective turn check (spec 2026-11-11 turn check §5): run `artmind.turn_check.open_items` over stored benchmark
turns and cross the open items with the graded must items missed. Counts and qids only, never question text.

uv run python scripts/turn_check_retro.py <vault> <run8>[,<run8>...] [--detail]

Grades come from the vault's `logs/compare-*.json` (`traces compare --grade`), joined on (run, seq).
"""
import glob
import json
import os
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from artmind.turn_check import KINDS, open_items  # noqa: E402


def _json(text, default):
    try:
        value = json.loads(text or "null")
    except ValueError:
        return default
    return value if isinstance(value, type(default)) else default


def grades(vault: Path) -> dict:
    out = {}
    for f in sorted(glob.glob(str(vault / ".artmind" / "logs" / "compare-*.json")), key=os.path.getmtime):
        for run, rows in (json.load(open(f)).get("grades") or {}).items():
            for r in rows:
                out[(run[:8], r["seq"])] = (r["qid"], len(r.get("must_missed") or []))
    return out


def turns(db, run8: str):
    for trace_id, seq in db.execute("select trace_id, benchmark_seq from trace_turns where benchmark_run_id like ?",
                                    (run8 + "%",)):
        spans = [{"command": c, "args": _json(a, {}), "rows": r, "rows_total": t, "truncated": bool(tr),
                  "evidence": _json(e, {})}
                 for c, a, r, t, tr, e in db.execute(
                     "select command, args, rows, rows_total, truncated, evidence from trace_spans "
                     "where trace_id = ? order by started_at", (trace_id,))]
        yield seq, [s for s in spans if (s["command"] or "").startswith(("query", "db"))]


def mean(xs):
    return round(statistics.mean(xs), 2) if xs else None


def main(argv):
    vault, runs = Path(argv[0]).expanduser(), argv[1].split(",")
    detail = "--detail" in argv
    g = grades(vault)
    db = sqlite3.connect(vault / ".artmind" / "data" / "document_registry.db")
    records = []  # (run, qid, missed, Counter of kinds, details)
    for run in runs:
        for seq, spans in turns(db, run):
            if (run, seq) not in g:
                continue
            qid, missed = g[(run, seq)]
            items = open_items(spans)
            records.append((run, qid, missed, Counter(i["kind"] for i in items),
                            Counter(f"{i['kind']}:{i['detail']}" for i in items if i["kind"] == "next_unfollowed")))
    print(json.dumps({"turns": len(records), "runs": [r for r in runs if any(x[0] == r for x in records)],
                      "missed_mean": mean([x[2] for x in records])}))
    any_open = [x[2] for x in records if x[3]]
    none_open = [x[2] for x in records if not x[3]]
    print(json.dumps({"kind": "any", "turns_open": len(any_open), "missed_open": mean(any_open),
                      "turns_clear": len(none_open), "missed_clear": mean(none_open)}))
    by_q = defaultdict(list)
    for x in records:
        by_q[x[1]].append(x)
    for kind in KINDS:
        with_k = [x[2] for x in records if x[3][kind]]
        without = [x[2] for x in records if not x[3][kind]]
        # within the same question: runs where the kind was open minus runs where it was not
        diffs = []
        for rows in by_q.values():
            a = [x[2] for x in rows if x[3][kind]]
            b = [x[2] for x in rows if not x[3][kind]]
            if a and b:
                diffs.append(statistics.mean(a) - statistics.mean(b))
        print(json.dumps({"kind": kind, "turns_open": len(with_k), "items": sum(x[3][kind] for x in records),
                          "missed_open": mean(with_k), "missed_clear": mean(without),
                          "paired_questions": len(diffs), "paired_diff": mean(diffs),
                          "paired_worse": sum(d > 0 for d in diffs), "paired_better": sum(d < 0 for d in diffs)}))
    nexts = Counter()
    for x in records:
        nexts.update(x[4])
    print(json.dumps({"next_unfollowed_by_command": dict(nexts.most_common(12))}))
    if detail:
        for x in sorted(records, key=lambda x: (x[1], x[0])):
            print(json.dumps({"qid": x[1], "run": x[0], "missed": x[2], "open": dict(x[3])}))


if __name__ == "__main__":
    main(sys.argv[1:])
