#!/usr/bin/env python3
"""Summarize a refine-pipeline pipeline_report.json for the three review gates.

Usage:
    python3 summarize_gates.py <pipeline_report.json> [--full]

Works on both propose-mode reports (has proposals to review) and apply-mode
reports (has stats/counts of what actually happened). Prints a compact,
consistent view instead of hand-rolled one-off `python3 -c` parsing.

Deliberately avoids silent `.get(..., default)` chains for structural keys:
if the CLI's report shape changes, this should error loudly (KeyError) so
the drift is visible, rather than quietly printing zeros.
"""
import json
import sys
from collections import Counter

MISSING = "‹missing key — report schema may have changed›"


def _get(d, key):
    if key not in d:
        return MISSING
    return d[key]


def truncate(s, n=200):
    if not isinstance(s, str):
        return s
    return s if len(s) <= n else s[: n - 1] + "…"


def summarize_merge_gate(domain, merge):
    proposed = _get(merge, "proposed_merges")
    print(f"  Gate 1 — merges ({len(proposed)} proposed)")
    if merge.get("stats"):
        s = merge["stats"]
        print(f"    apply stats: merged={s.get('merged')} skipped={s.get('skipped')} errors={s.get('errors')}")
    by_canonical = Counter(proposed.values())
    big_clusters = {c: n for c, n in by_canonical.items() if n > 5}
    if big_clusters:
        print("    clusters >5 aliases (spot-check these against source chunks):")
        for canonical, n in sorted(big_clusters.items(), key=lambda kv: -kv[1]):
            aliases = [a for a, c in proposed.items() if c == canonical]
            print(f"      [{n}] -> {canonical}: {aliases}")
    for alias, canonical in sorted(proposed.items()):
        print(f"    {alias!r} -> {canonical!r}")


def summarize_conflicts_gate(label, conflicts):
    proposals = _get(conflicts, "proposals")
    materialized = _get(conflicts, "conflicts")
    verdict_counts = Counter(p["verdict"]["verdict"] for p in proposals)
    print(f"  Gate 2 — conflicts [{label}] ({len(proposals)} candidates evaluated, "
          f"{len(materialized)} materialized)")
    print(f"    verdicts: {dict(verdict_counts)}")
    if conflicts.get("warning_missing_refine"):
        print(f"    warning_missing_refine: {conflicts['warning_missing_refine']} "
              "(expected pre-merge in propose mode)")
    live = [p for p in proposals if p["verdict"]["verdict"] not in ("superseded", "no_conflict")]
    for p in live:
        v = p["verdict"]
        pair = p["pair"]
        print(f"    [{v['verdict']}/{v.get('severity')}] {pair['name_a']} <-> {pair['name_b']} "
              f"| aspect={v.get('aspect')!r}")


def summarize_consolidate_gate(domain, consolidate):
    total = _get(consolidate, "candidates_total")
    rows = _get(consolidate, "rows")
    header = f"  Gate 3 — consolidation ({len(rows)} sample rows shown, candidates_total={total}"
    if consolidate.get("dry_run") is False:
        header += f", written={consolidate.get('written')}/{consolidate.get('examined')}"
    header += ")"
    print(header)
    for r in rows:
        name = r.get("name") or r.get("entity_id")
        old = truncate(r.get("old_description") or r.get("description_raw") or "")
        new = truncate(r.get("new_description") or "")
        print(f"    entity: {name}")
        print(f"      OLD: {old}")
        print(f"      NEW: {new}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    report = json.loads(open(sys.argv[1]).read())
    full = "--full" in sys.argv

    print(f"mode={report.get('mode')} domains={report.get('domains')} model={report.get('model')}")
    for domain, pd in report["per_domain"].items():
        print(f"\n=== {domain} ===")
        summarize_merge_gate(domain, pd["merge"])
        summarize_conflicts_gate(domain, pd["conflicts"])
        summarize_consolidate_gate(domain, pd["consolidate"])
        if pd.get("embed"):
            print(f"  embed: {pd['embed']}")

    cross = report.get("cross_domain_conflicts")
    if cross:
        print(f"\n=== cross-domain ===")
        summarize_conflicts_gate("cross", cross)

    if report.get("report_file"):
        print(f"\nreport_file: {report['report_file']}")
    if report.get("apply_with"):
        print(f"apply_with: {report['apply_with']}")


if __name__ == "__main__":
    main()
