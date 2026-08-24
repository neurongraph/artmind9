#!/usr/bin/env python
"""Phase 3 exit gate — the vertical slice, run live.

Ingests the three `interest_rate_schedule_*` documents from
`banking.reference` and asserts the projection they must produce:

    ONE :Entity for "SmartSaver Account Tier 2 Rate"
      holding rate_value 4.50          (March — the latest valid_from)
      with _temporal_props including "rate_value"
      backed by three :Observation nodes via AGGREGATES
      and NO :Conflict                 (the three windows do not overlap)

Two modes, because the two halves of the pipeline have different dependencies:

    --full          the real thing: chunk extraction and the per-document
                    canonicalization pass both call an LLM, and the name
                    vocabulary calls the embedding service. Needs Ollama (or
                    another configured provider) and Neo4j.

    --fixtures      the deterministic half only. Extraction output is staged
                    from `test/data/phase3_slice/`, hand-written to mirror what
                    the three documents actually say, and everything from the
                    observation write onward is the real code path against a
                    real Neo4j. Proves the projection; does not exercise the
                    two LLM steps.

Usage:
    ARTMIND_KG_NEO4J_URI=bolt://127.0.0.1:7687 python scripts/phase3_vertical_slice.py --fixtures
    python scripts/phase3_vertical_slice.py --full --vault ~/vault
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DOMAIN = "banking.reference"
TIER2 = "SmartSaver Account Tier 2 Rate"
CLASS = "RATE_ENTRY"

CORPUS = Path(__file__).resolve().parent.parent / "banking_document_corpus" / "reference"
FIXTURES = Path(__file__).resolve().parent.parent / "test" / "data" / "phase3_slice"

GREEN, RED, RESET = "\033[32m", "\033[31m", "\033[0m"


class Gate:
    def __init__(self) -> None:
        self.failures: list[str] = []

    def check(self, label: str, ok: bool, detail: str = "") -> None:
        mark = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
        print(f"  {mark}  {label}" + (f"  — {detail}" if detail else ""))
        if not ok:
            self.failures.append(f"{label} ({detail})" if detail else label)

    def report(self) -> int:
        print()
        if self.failures:
            print(f"{RED}EXIT GATE FAILED{RESET} — {len(self.failures)} check(s):")
            for f in self.failures:
                print(f"  - {f}")
            return 1
        print(f"{GREEN}EXIT GATE PASSED{RESET}")
        return 0


def _clean(session) -> None:
    """Remove anything a prior slice run left behind for these three docs."""
    doc_ids = [r["id"] for r in session.run(
        "MATCH (d:Document) WHERE d.name STARTS WITH 'interest_rate_schedule' RETURN d.id AS id"
    ).data()]
    for doc_id in doc_ids:
        session.run("MATCH (o:Observation {doc_id: $id}) DETACH DELETE o", id=doc_id).consume()
        session.run("MATCH (c:DocChunk {doc_id: $id}) DETACH DELETE c", id=doc_id).consume()
        session.run("MATCH (d:Document {id: $id}) DETACH DELETE d", id=doc_id).consume()
    session.run(
        "MATCH (e:Entity {domain: $d}) WHERE e.entity_class = $c DETACH DELETE e",
        d=DOMAIN, c=CLASS,
    ).consume()
    session.run(
        "MATCH (c:Conflict {_source: 'projection'}) DETACH DELETE c"
    ).consume()


def run_fixtures(gate: Gate) -> None:
    """Stage hand-written extraction output, then run the real pipeline from
    canonicalization onward.

    Only the *model call* is stubbed. `canonicalize_document` (including
    `collect_names` and the mapping fold), `_document_valid_time`,
    `_build_observations`, the observation write, the affected-key union, the
    projection rebuild and the GC are all the real code against a real Neo4j.

    Worth noting what the fixture names make the pass earn: the key function
    alone maps January's "SmartSaver Account Tier 2 Rate — 4.70% AER (...)"
    to `smartsaver account tier 2 rate` but February's "SmartSaver Tier 2 —
    4.60% AER" to `smartsaver tier 2`. Without canonicalization these are two
    different entities, and the gate fails.
    """
    import artmind.extraction as extraction
    from artmind.canonicalize import canonicalize_document
    from artmind.graph_query import neo4j_session
    from artmind.ingest import (
        _build_observations,
        _document_valid_time,
        _parse_md_frontmatter,
        commit_to_graph,
    )
    from artmind.setup import _setup_neo4j
    from artmind.temporal import load_schema

    staged_root = Path("/tmp/phase3_slice_kg")
    if staged_root.exists():
        shutil.rmtree(staged_root)
    staged_root.mkdir(parents=True)

    with neo4j_session() as session:
        _setup_neo4j(session, 768)
        _clean(session)

    manifests = sorted(FIXTURES.glob("*.json"))
    if not manifests:
        raise SystemExit(f"No fixtures found in {FIXTURES}")

    schema = load_schema(DOMAIN)
    if not (schema.get("temporal") or {}).get("document"):
        raise SystemExit(
            f"No temporal mapping for {DOMAIN} — is ARTMIND_HOME seeded? Run `artmind init`."
        )

    print(f"\nCommitting {len(manifests)} staged document(s) — real commit path, real Neo4j\n")
    real_extract = extraction.extract_with_retry

    for manifest_path in manifests:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        document = dict(manifest["document"])
        source = Path(manifest["source_markdown"])

        # Real date lifting, from the real markdown.
        frontmatter, _ = _parse_md_frontmatter(source.read_text(encoding="utf-8"))
        document.update(_document_valid_time(source, frontmatter, schema))
        gate.check(
            f"{source.name}: valid_from lifted",
            bool(document.get("_valid_from")),
            f"_valid_from={document.get('_valid_from')!r}",
        )

        # Real canonicalization pass; only the model call is stubbed.
        calls = []

        def stub(step_name, model, prompt, debug_dir=None, _r=manifest["canonicalization_response"]):
            calls.append(step_name)
            return _r, True

        extraction.extract_with_retry = stub
        try:
            canonical_names = canonicalize_document(
                manifest["entities"], schema=schema, vocabulary=[], model="stub",
            )
        finally:
            extraction.extract_with_retry = real_extract
        gate.check(f"{source.name}: ONE canonicalization call", len(calls) == 1, f"{len(calls)} calls")

        # Real observation building.
        observations = _build_observations(
            manifest["entities"], manifest["properties"], canonical_names, schema, document,
        )
        gate.check(
            f"{source.name}: {len(manifest['entities'])} entities → observations",
            len(observations) == len(manifest["entities"]),
            f"{len(observations)} observations",
        )

        doc_dir = staged_root / document["id"]
        doc_dir.mkdir(parents=True, exist_ok=True)
        for name, payload in (
            ("document", document), ("chunks", manifest["chunks"]),
            ("observations", observations), ("relationships", []),
        ):
            (doc_dir / f"{name}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

        # Deferred, exactly like a directory ingest: one full rebuild at the end.
        ok = commit_to_graph(doc_dir, DOMAIN, defer_rebuild=True)
        gate.check(f"{source.name}: committed", ok)

    from artmind.ingest import rebuild_projection

    summary = rebuild_projection(DOMAIN)
    print(f"\nDeferred full rebuild: {summary}\n")


def run_full(gate: Gate, vault: Path) -> None:
    """The real thing, LLM and all."""
    from artmind.ingest import ingest_file, ingest_to_kg, rebuild_projection
    from artmind.graph_query import neo4j_session
    from artmind.setup import _setup_neo4j
    from utils.functions import load_env

    env = load_env()
    text_model = env.get("ARTMIND_KG_LLM_MODEL", "ministral-3:14b")
    embed_model = env.get("ARTMIND_KG_EMBEDDINGS_MODEL", "nomic-embed-text:latest")

    with neo4j_session() as session:
        _setup_neo4j(session, 768)
        _clean(session)

    sources = sorted(CORPUS.glob("interest_rate_schedule_*.md")) + [CORPUS / "interest_rate_schedule_2026.md"]
    sources = sorted({p for p in sources if p.exists()})
    print(f"\nIngesting {len(sources)} document(s) with model={text_model}\n")

    for source in sources:
        target = vault / source.name
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        result = ingest_file(target, "gemma4:e4b", DOMAIN, chunk_size=6000)
        gate.check(f"ingest {source.name}", result.get("status") == "ok", result.get("error", ""))
        if result.get("status") == "ok":
            ok = ingest_to_kg(
                result, DOMAIN, text_model, embed_model, 6000, defer_rebuild=True
            )
            gate.check(f"extract+commit {source.name}", ok)

    summary = rebuild_projection(DOMAIN)
    print(f"\nDeferred full rebuild: {summary}\n")


def assert_gate(gate: Gate) -> None:
    from artmind.graph_query import read_session
    from artmind.observations import aggregate_key, entity_id, normalize_name

    key = aggregate_key(TIER2, CLASS, DOMAIN)
    eid = entity_id(key)

    print("Exit gate:\n")
    with read_session() as session:
        # 1. ONE :Entity for the Tier 2 rate.
        rows = session.run(
            """
            MATCH (e:Entity {domain: $d, entity_class: $c})
            WHERE e.key = $key
            RETURN e.id AS id, e.name AS name, e.rate_value AS rate_value,
                   e._temporal_props AS temporal_props, e.aliases AS aliases,
                   e.description AS description
            """,
            d=DOMAIN, c=CLASS, key="|".join(key),
        ).data()
        gate.check("exactly ONE :Entity for the aggregate key", len(rows) == 1, f"found {len(rows)}")
        if not rows:
            # Show what DID land, so a failure is diagnosable rather than blank.
            near = session.run(
                "MATCH (e:Entity {domain: $d, entity_class: $c}) RETURN e.name AS n ORDER BY n",
                d=DOMAIN, c=CLASS,
            ).data()
            print(f"\n    entities in {DOMAIN}/{CLASS}: {[r['n'] for r in near]}\n")
            return

        entity = rows[0]
        gate.check("its id is the hash of the key", entity["id"] == eid)
        gate.check(
            "its name normalizes to the Tier 2 rate",
            normalize_name(entity["name"]) == normalize_name(TIER2),
            f"name={entity['name']!r}",
        )

        # 2. It holds the March rate — the latest document valid_from.
        gate.check(
            "rate_value is 4.50 (March, the latest valid_from)",
            float(entity["rate_value"]) == 4.50 if entity["rate_value"] is not None else False,
            f"rate_value={entity['rate_value']!r}",
        )

        # 3. _temporal_props declares that it varies.
        temporal = list(entity["temporal_props"] or [])
        gate.check(
            '_temporal_props includes "rate_value"',
            "rate_value" in temporal,
            f"_temporal_props={temporal}",
        )

        # 4. Three observations behind it, via AGGREGATES.
        behind = session.run(
            """
            MATCH (:Entity {id: $id})-[:AGGREGATES]->(o:Observation)
            RETURN count(o) AS c, collect(o.rate_value) AS rates,
                   collect(o._doc_valid_from) AS dates
            """,
            id=eid,
        ).single()
        gate.check(
            "three :Observation nodes behind it via AGGREGATES",
            behind["c"] == 3,
            f"count={behind['c']}, rates={behind['rates']}, dates={sorted(d for d in behind['dates'] if d)}",
        )

        # 5. No conflict — the three windows do not overlap.
        conflicts = session.run(
            "MATCH (c:Conflict)-[:CONFLICT_OF]->(:Entity {id: $id}) RETURN collect(c.property) AS p",
            id=eid,
        ).single()["p"]
        gate.check(
            "no :Conflict (the three windows are disjoint)",
            not conflicts,
            f"conflicts on {conflicts}" if conflicts else "",
        )

        # ── invariants the gate does not name but the phase depends on ──
        labels = session.run(
            "MATCH (o:Observation {domain: $d}) UNWIND labels(o) AS l "
            "RETURN collect(DISTINCT l) AS ls", d=DOMAIN,
        ).single()["ls"]
        gate.check(
            "observations carry no :Entity and no class label",
            set(labels or []) <= {"Observation"},
            f"labels={sorted(labels or [])}",
        )
        accreted = session.run(
            "MATCH (e:Entity {domain: $d}) WHERE e.description CONTAINS ' | ' RETURN count(e) AS c",
            d=DOMAIN,
        ).single()["c"]
        gate.check("no accreted ' | ' descriptions", accreted == 0, f"{accreted} entities")
        nulls = session.run(
            "MATCH (e:Entity {domain: $d}) WHERE e.embedding IS NULL AND e.embedding_stale IS NULL "
            "RETURN count(e) AS c", d=DOMAIN,
        ).single()["c"]
        gate.check(
            "no entity is both un-embedded and unflagged",
            nulls == 0,
            f"{nulls} entities invisible to the sweep",
        )

    print(f"\n  entity name : {entity['name']!r}")
    print(f"  aliases     : {entity['aliases']}")
    print(f"  description : {(entity['description'] or '')[:120]}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--full", action="store_true", help="Real ingest, LLM and embeddings (needs Ollama)")
    mode.add_argument("--fixtures", action="store_true", help="Staged extraction output; projection only")
    parser.add_argument("--vault", type=Path, default=None, help="Vault dir for --full")
    args = parser.parse_args()

    gate = Gate()
    if args.full:
        if not args.vault:
            parser.error("--full requires --vault")
        run_full(gate, args.vault)
    else:
        run_fixtures(gate)

    assert_gate(gate)
    return gate.report()


if __name__ == "__main__":
    raise SystemExit(main())
