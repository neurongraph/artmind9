"""Guarded same-as merges from same_as_candidates findings, shared by the CLI export and the admin console."""

from __future__ import annotations

NOTE = ("refine-graph resolves names to entities; its stats (skipped_ambiguous, skipped_cross_class) are "
        "the final word. This file holds names from your vault: keep it out of the code checkout.")


def build_merges(findings, min_score: float = 0.0, only_keys=None) -> tuple[dict[str, str], list[dict], dict[str, int]]:
    """{alias: canonical} by score; skips chains and names shared by two entities.

    `only_keys` limits which candidates become merges, while the shared-name check still
    sees every candidate, so picking one card cannot hide that its name is ambiguous."""
    items = sorted((f for f in findings if f.detector == "same_as_candidates" and f.diff != "gone"
                    and f.score >= min_score), key=lambda f: (-f.score, f.key))
    ids_by_name: dict[str, set] = {}
    for f in items:
        for side in ("alias", "canonical"):
            ids_by_name.setdefault(f.stats[side]["name"], set()).add(f.stats[side].get("_id"))
    merges: dict[str, str] = {}
    candidates: list[dict] = []
    skipped = {"chain": 0, "ambiguous_name": 0}
    for f in items:
        if only_keys is not None and f.key not in only_keys:
            continue
        alias, canonical = f.stats["alias"]["name"], f.stats["canonical"]["name"]
        if alias == canonical or alias in merges:
            continue
        if len(ids_by_name[alias]) > 1 or len(ids_by_name[canonical]) > 1:
            skipped["ambiguous_name"] += 1
            continue
        if alias in merges.values() or canonical in merges:
            skipped["chain"] += 1
            continue
        merges[alias] = canonical
        candidates.append({"alias": alias, "canonical": canonical, "class": f.stats["canonical"]["class"],
                           "score": f.score, "rule": f.stats.get("rule")})
    return merges, candidates, skipped
