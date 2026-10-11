"""Proactive shape sampling (spec 2026-10-11 §7.5 `--sample`): random chunks
of a domain, no failing question needed, with two mechanical signals --
`lost_at_commit` (a staging observation with no graph entity extracted from
the chunk) and `isolated` (an observation no staging relationship touches).
The reviewer grades the actual shape with `artmind review shape --chunk`."""

import random
from pathlib import Path

from artmind.reviews import shape


def _cf(value) -> str:
    return str(value or "").strip().casefold()


def all_chunks(domain: str) -> list[tuple[str, Path]]:
    import paths

    root = paths.KG_DIR / domain
    if not root.is_dir():
        return []
    out = []
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        for chunk in shape._read_json(folder / "chunks.json", []):
            if chunk.get("id"):
                out.append((chunk["id"], folder))
    return out


def signals(staging: dict, graph: dict | None) -> dict:
    observations = staging["observations"]
    touched = {_cf(r.get("source_name")) for r in staging["relationships"]} | \
              {_cf(r.get("target_name")) for r in staging["relationships"]}
    isolated = sorted({o["name"] for o in observations
                       if _cf(o.get("name")) not in touched and _cf(o.get("canonical_name")) not in touched})
    if graph is None:
        return {"isolated": isolated}
    here = {_cf(n.get("name")) for n in graph["nodes"] if n.get("extracted_here")}
    lost = sorted({o["name"] for o in observations
                   if _cf(o.get("name")) not in here and _cf(o.get("canonical_name")) not in here})
    return {"lost_at_commit": lost, "isolated": isolated}


def sample(domain: str, n: int, *, seed: int | None = None, with_graph: bool = True) -> dict:
    population = all_chunks(domain)
    picked = random.Random(seed).sample(population, min(n, len(population)))
    out = []
    for chunk_id, folder in picked:
        staged = shape._staging_from_folder(folder, chunk_id)
        graph = shape.graph_for_chunks([domain], [chunk_id]) if with_graph else None
        out.append({
            "chunk_id": chunk_id,
            "document": staged["document"].get("path") or staged["document"].get("name"),
            "sentences": len(shape.sentences(staged["chunk"].get("text"))),
            "observations": len(staged["observations"]),
            "relationships": len(staged["relationships"]),
            "graph_entities": sum(1 for x in graph["nodes"] if x.get("extracted_here")) if graph else None,
            "signals": signals(staged, graph),
            "next": f"artmind review shape --domain {domain} --chunk {chunk_id}",
        })
    return {"domain": domain, "seed": seed, "population": len(population), "sampled": out}
