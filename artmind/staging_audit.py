"""Read-only audits of the KG staging folders (`KG_DIR/<domain>/<stem>/`).

`empty_extractions` lists notes whose staged extraction is empty although
their chunk text is not -- what a re-extraction that silently came back empty
leaves behind (spec 2026-10-18-extraction-loss-guard-design.md §3.3). A
chunks-only domain (`graph_extraction: false`) is skipped: empty is its
intended result. Reads
staging only: no registry, no Neo4j, no LLM.
"""
import json
from pathlib import Path

from artmind.schema_validate import domain_graph_extraction


def _in_family(domain: str, wanted: list[str] | None) -> bool:
    return not wanted or any(domain == w or domain.startswith(w + ".") for w in wanted)


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def empty_extractions(kg_dir: Path, *, domains: list[str] | None = None, min_chars: int = 200) -> dict:
    """`{"notes": [{path, domain, chars, chunks}], "scanned": N}` for staging
    folders whose `observations.json` is `[]` while their `chunks.json` text
    totals at least `min_chars`. `scanned` counts folders with an
    `observations.json`; unreadable folders are skipped."""
    kg_dir = Path(kg_dir)
    notes: list[dict] = []
    scanned = 0
    if not kg_dir.is_dir():
        return {"notes": notes, "scanned": scanned}
    for domain_dir in sorted(p for p in kg_dir.iterdir() if p.is_dir()):
        if not _in_family(domain_dir.name, domains):
            continue
        if not domain_graph_extraction(domain_dir.name):
            continue  # empty is correct in a chunks-only domain
        for folder in sorted(p for p in domain_dir.iterdir() if p.is_dir()):
            if not (folder / "observations.json").is_file():
                continue
            scanned += 1
            try:
                observations = _read_json(folder / "observations.json")
                chunks = _read_json(folder / "chunks.json")
            except (OSError, ValueError):
                continue
            if observations != [] or not isinstance(chunks, list):
                continue
            texts = [c.get("text") or "" for c in chunks if isinstance(c, dict)]
            chars = sum(len(t) for t in texts)
            if chars < min_chars:
                continue
            try:
                document = _read_json(folder / "document.json")
            except (OSError, ValueError):
                document = {}
            notes.append({
                "path": document.get("path") or folder.name,
                "domain": document.get("_domain") or domain_dir.name,
                "chars": chars,
                "chunks": len(texts),
            })
    return {"notes": notes, "scanned": scanned}
