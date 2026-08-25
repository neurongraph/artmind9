#!/usr/bin/env python
"""Phase 3 exit gate — the vertical slice, run live.

Ingests the three `interest_rate_schedule_*` documents from
`banking.reference` and asserts the projection they must produce:

    ONE :Entity for "SmartSaver Account Tier 2 Rate"
      holding rate_value 4.50          (March — the latest valid_from)
      with _temporal_props including "rate_value"
      backed by all three documents via AGGREGATES (one observation per
        chunk, so three documents produce three or more observations)
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
import logging
import shutil
import sys
from pathlib import Path

# The driver warns about labels/relationship types that do not exist yet — on a
# fresh graph that is every query the gate makes, and it buries the results.
logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)

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


# The rebuild and the relationship writer depend on these. AuraDB exposes a
# curated subset of APOC, so a missing one is a real possibility — and without
# this check it surfaces as an opaque ProcedureNotFound mid-rebuild.
REQUIRED_APOC = (
    "apoc.create.addLabels",
    "apoc.create.removeProperties",
    "apoc.merge.relationship",
)


def _check_apoc(session) -> None:
    try:
        available = {r["name"] for r in session.run("SHOW PROCEDURES YIELD name RETURN name").data()}
    except Exception as e:
        print(f"  (could not enumerate procedures: {e}); continuing")
        return
    missing = [p for p in REQUIRED_APOC if p not in available]
    if missing:
        raise SystemExit(
            "This database does not expose the APOC procedures the projection needs:\n"
            + "".join(f"  - {p}\n" for p in missing)
            + "\nOn AuraDB, check the supported-APOC list for your tier. On a local\n"
              "install, drop the APOC plugin jar into the plugins/ directory and set\n"
              "dbms.security.procedures.unrestricted=apoc.*"
        )


def _preflight(session) -> dict:
    """Report what a run would touch, BEFORE touching it.

    Worth reading on a machine that still holds the Phase 0 baseline: this
    graph is pre-cutover, so it contains entities written by the old accretive
    upsert. Those carry no `key` property, and `_clean` leaves them alone —
    but the three rate-schedule Documents themselves will be replaced.
    """
    counts = session.run(
        """
        CALL () { MATCH (d:Document) WHERE d.name STARTS WITH 'interest_rate_schedule'
                  RETURN count(d) AS documents }
        CALL () { MATCH (e:Entity {domain: $d}) WHERE e.entity_class = $c AND e.key IS NOT NULL
                  RETURN count(e) AS phase3_entities }
        CALL () { MATCH (e:Entity {domain: $d}) WHERE e.entity_class = $c AND e.key IS NULL
                  RETURN count(e) AS legacy_entities }
        CALL () { MATCH (o:Observation {domain: $d}) RETURN count(o) AS observations }
        RETURN documents, phase3_entities, legacy_entities, observations
        """,
        d=DOMAIN, c=CLASS,
    ).single()
    return dict(counts)


def _clean(session) -> None:
    """Remove what a PRIOR RUN OF THIS SCRIPT left behind — and nothing else.

    Entity deletion is scoped to `e.key IS NOT NULL`, i.e. entities the Phase 3
    projection produced. A pre-cutover graph's entities were written by the old
    accretive upsert and carry no `key`, so they survive untouched: this script
    must not quietly destroy the Phase 0 baseline the scorecard measures
    against.

    The three rate-schedule Documents (and their chunks and observations) ARE
    replaced — that is the point of the run — so take a snapshot first if their
    current state matters to you.
    """
    doc_ids = [r["id"] for r in session.run(
        "MATCH (d:Document) WHERE d.name STARTS WITH 'interest_rate_schedule' RETURN d.id AS id"
    ).data()]
    for doc_id in doc_ids:
        session.run("MATCH (o:Observation {doc_id: $id}) DETACH DELETE o", id=doc_id).consume()
        session.run("MATCH (c:DocChunk {doc_id: $id}) DETACH DELETE c", id=doc_id).consume()
        session.run("MATCH (d:Document {id: $id}) DETACH DELETE d", id=doc_id).consume()
    session.run(
        "MATCH (e:Entity {domain: $d}) WHERE e.entity_class = $c AND e.key IS NOT NULL "
        "DETACH DELETE e",
        d=DOMAIN, c=CLASS,
    ).consume()
    session.run("MATCH (c:Conflict {_source: 'projection'}) DETACH DELETE c").consume()


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
        _check_apoc(session)
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


def _reset_content_fingerprint(paths: list[Path]) -> int:
    """Drop `_content_sha256` from the vault files this run is about to ingest.

    `_clean` has just deleted these documents' observations from the graph. The
    vault frontmatter still claims a content hash, so the next ingest compares
    equal, returns the `metadata_only` fast path, and writes NO observations —
    and the deferred full rebuild then correctly deletes every entity whose
    observations are gone. A clean run empties the projection and refills
    nothing.

    That is not a bug in the fast path: it is right to skip extraction when the
    graph already holds the prior version's observations. It is a bug to delete
    that graph state while leaving the fingerprint asserting it is present. So
    the two are reset together.
    """
    from artmind.document_identity import serialize_frontmatter
    from artmind.ingest import _parse_md_frontmatter

    reset = 0
    for path in paths:
        try:
            meta, body = _parse_md_frontmatter(path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  (could not read {path.name}: {e})")
            continue
        if "_content_sha256" not in meta:
            continue
        meta.pop("_content_sha256", None)
        path.write_text(f"---\n{serialize_frontmatter(meta)}---\n\n{body}", encoding="utf-8")
        reset += 1
    return reset


def run_full(gate: Gate, vault: Path) -> None:
    """The real thing: chunk extraction, the name vocabulary's ANN, the
    per-document canonicalization pass, and the post-commit embed sweep all
    hit live services."""
    import os

    from artmind.graph_query import neo4j_session
    from artmind.ingest import ingest_file, ingest_to_kg, rebuild_projection
    from artmind.setup import _setup_neo4j
    from artmind.temporal import load_schema
    from utils.functions import load_env

    env = load_env()
    text_model = env.get("ARTMIND_KG_LLM_MODEL", "ministral-3:14b")
    embed_model = env.get("ARTMIND_KG_EMBEDDINGS_MODEL", "nomic-embed-text:latest")
    image_model = env.get("ARTMIND_IMAGE_MODEL", "gemma4:e4b")

    # The vault-native path (Phase 2) is only taken for a .md file INSIDE
    # ARTMIND_VAULT_DIR. Outside it, ingest silently falls back to the
    # pre-Phase-2 path-keyed flow — which still works, but is not what this
    # gate is meant to exercise, and the difference is invisible in the output.
    vault_dir = env.get("ARTMIND_VAULT_DIR") or os.environ.get("ARTMIND_VAULT_DIR")
    if not vault_dir:
        raise SystemExit(
            "ARTMIND_VAULT_DIR is not set. Set it in ~/.artmind/.env (Phase 0 step) "
            "so the vault-native ingest path is taken."
        )
    vault_root = Path(vault_dir).expanduser().resolve()
    vault = vault.expanduser().resolve()
    if vault_root not in (vault, *vault.parents):
        raise SystemExit(
            f"--vault {vault} is not inside ARTMIND_VAULT_DIR ({vault_root}).\n"
            "Ingest would take the binary/ad-hoc path instead of the vault-native one."
        )

    schema = load_schema(DOMAIN)
    if not (schema.get("temporal") or {}).get("document"):
        raise SystemExit(
            f"No temporal mapping for {DOMAIN} — run `artmind init` to seed the run folder."
        )

    with neo4j_session() as session:
        _check_apoc(session)
        _setup_neo4j(session, int(env.get("ARTMIND_KG_EMBEDDING_DIMENSIONS", "768")))
        before = _preflight(session)
        print(f"\nPreflight: {before}")
        print("  (entities with no `key` are pre-cutover and are NOT deleted)\n")
        _clean(session)

    sources = sorted({p for p in CORPUS.glob("interest_rate_schedule_*.md") if p.exists()})
    if len(sources) != 3:
        raise SystemExit(f"Expected 3 rate schedules in {CORPUS}, found {len(sources)}")

    vault.mkdir(parents=True, exist_ok=True)
    targets = [vault / s.name for s in sources]
    for source, target in zip(sources, targets):
        if not target.exists():
            shutil.copy2(source, target)

    reset = _reset_content_fingerprint(targets)
    print(f"Reset the content fingerprint on {reset} vault file(s) — the graph state "
          f"they described was just cleaned.")
    print(f"Ingesting {len(sources)} document(s) | text={text_model} embed={embed_model}\n")

    commits = 0
    for source in sources:
        target = vault / source.name
        result = ingest_file(target, image_model, DOMAIN, chunk_size=6000)
        ok = result.get("status") == "ok"
        gate.check(f"ingest {source.name}", ok, result.get("error", ""))
        if not ok:
            continue
        # Deferred, exactly like a directory ingest: one full rebuild at the end.
        committed = ingest_to_kg(
            result, DOMAIN, text_model, embed_model, 6000, defer_rebuild=True
        )
        gate.check(f"extract+commit {source.name}", committed)
        if committed:
            commits += 1

    # A deferred full rebuild after a failed extraction is GUARANTEED
    # destructive: `_clean` removed these documents' observations, so every key
    # they fed now has zero and the rebuild correctly deletes its :Entity. The
    # projection is behaving properly; running it here would not be. Abort with
    # the graph as extraction left it, and say how to restore.
    if commits < len(sources):
        print(
            f"\n  {RED}ABORTED before the projection rebuild{RESET} — "
            f"{len(sources) - commits} of {len(sources)} document(s) failed to commit.\n"
            f"  A full rebuild now would delete every entity these documents fed,\n"
            f"  because their observations were cleaned and never rewritten.\n"
            f"  Fix the extraction failure above, then re-run; or restore from your snapshot."
        )
        return

    summary = rebuild_projection(DOMAIN)
    print(f"\nDeferred full rebuild + embed sweep: {summary}\n")
    gate.check(
        "the embed sweep embedded at least one entity",
        summary.get("embedded", 0) > 0,
        f"embedded={summary.get('embedded')}",
    )


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
        # The phase plan says "three :Observation nodes behind it", written
        # before real chunking was in play. The spec is one observation per
        # (doc_version, CHUNK, entity-identity), and January's long-form
        # schedule mentions the Tier 2 rate in three separate chunks — so three
        # documents legitimately produce five observations.
        #
        # What the gate is actually asserting is "all three schedules feed this
        # one entity", so it counts DOCUMENTS and reports the observation count
        # alongside. Counting observations would make the gate a function of
        # the chunk size.
        behind = session.run(
            """
            MATCH (:Entity {id: $id})-[:AGGREGATES]->(o:Observation)
            RETURN count(o) AS observations, count(DISTINCT o.doc_id) AS documents,
                   collect(o.rate_value) AS rates, collect(o._doc_valid_from) AS dates
            """,
            id=eid,
        ).single()
        gate.check(
            "all THREE documents feed it via AGGREGATES",
            behind["documents"] == 3,
            f"documents={behind['documents']}, observations={behind['observations']}, "
            f"rates={behind['rates']}, dates={sorted(d for d in behind['dates'] if d)}",
        )

        # 5. No conflict — the three windows do not overlap.
        #
        # The parenthetical names what this is really testing: the three
        # schedules must not disagree with each other. But a :Conflict can also
        # arise WITHIN one document, and that is designed behaviour, not a
        # projection defect — docs/projection-pipeline.md's own worked example
        # is January disputing itself. January's schedule is 22 chunks and
        # describes the Tier 2 rate in four places (the rate table, the
        # competitor comparison, a savings strategy, an FAQ), so it has ample
        # room to disagree with itself.
        #
        # So the two are counted separately: a conflict whose evidence spans
        # more than one doc_id is cross-document and contradicts the disjoint
        # -windows claim; one confined to a single doc_id is intra-document.
        # Both are reported with the values in dispute, because "conflicts on
        # ['balance_max']" does not tell you whether the sources disagree about
        # the world or the extractor merely wrote 50000 one place and "£50k"
        # another.
        conflicts = session.run(
            """
            MATCH (c:Conflict {_source: 'projection'})-[:CONFLICT_OF]->(:Entity {id: $id})
            OPTIONAL MATCH (c)-[:EVIDENCE]->(o:Observation)
            RETURN c.property AS property, c.values AS values,
                   count(DISTINCT o.doc_id) AS docs
            ORDER BY property
            """,
            id=eid,
        ).data()
        cross = [c for c in conflicts if (c["docs"] or 0) > 1]
        within = [c for c in conflicts if (c["docs"] or 0) <= 1]

        def _describe(rows):
            return "; ".join(f"{r['property']}={r['values']}" for r in rows)

        gate.check(
            "no CROSS-DOCUMENT :Conflict (the three windows are disjoint)",
            not cross,
            _describe(cross) if cross else (
                f"{len(within)} intra-document conflict(s), none across documents"
            ),
        )
        if within:
            print(
                f"\n  note: {len(within)} conflict(s) confined to a single document — "
                "designed behaviour, not a window overlap:"
            )
            for row in within:
                print(f"        {row['property']} = {row['values']}")
            print(
                "        Each is one document describing the same fact two ways.\n"
                "        Worth reading as corpus/extractor quality, not projection\n"
                "        correctness — see the Conflicts section of phase3_inspect.py."
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
        # These two are scoped to `e.key IS NOT NULL` — entities the Phase 3
        # projection produced — for the same reason `_clean` is: a pre-cutover
        # graph holds entities written by the old accretive upsert, and those
        # are exactly what this run must NOT touch or be judged on. Scorecard
        # row 2 (accreted descriptions) goes to zero at the Phase 8 re-ingest,
        # not here.
        accreted = session.run(
            "MATCH (e:Entity {domain: $d}) WHERE e.key IS NOT NULL "
            "AND e.description CONTAINS ' | ' RETURN count(e) AS c",
            d=DOMAIN,
        ).single()["c"]
        gate.check(
            "no accreted ' | ' descriptions among projected entities",
            accreted == 0, f"{accreted} entities",
        )
        nulls = session.run(
            "MATCH (e:Entity {domain: $d}) WHERE e.key IS NOT NULL "
            "AND e.embedding IS NULL AND e.embedding_stale IS NULL "
            "RETURN count(e) AS c", d=DOMAIN,
        ).single()["c"]
        gate.check(
            "no projected entity is both un-embedded and unflagged",
            nulls == 0,
            f"{nulls} entities invisible to the sweep",
        )

        # Pre-cutover entities, reported rather than judged. A non-zero count
        # here is the Phase 0 baseline still sitting in the graph, which is
        # expected until Phase 8 wipes and re-ingests.
        legacy = session.run(
            "MATCH (e:Entity {domain: $d}) WHERE e.key IS NULL "
            "RETURN count(e) AS total, "
            "count(CASE WHEN e.description CONTAINS ' | ' THEN 1 END) AS accreted",
            d=DOMAIN,
        ).single()
        if legacy["total"]:
            print(
                f"\n  note: {legacy['total']} pre-cutover entities remain in {DOMAIN} "
                f"({legacy['accreted']} with accreted ' | ' descriptions).\n"
                f"        Not touched by this run, and not a gate failure — "
                f"scorecard row 2 clears at the Phase 8 re-ingest."
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

    from artmind.graph_query import _connection_settings

    settings = _connection_settings()
    print(f"\nNeo4j: {settings['uri']}  database={settings['database']}  user={settings['user']}")

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
