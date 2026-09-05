"""Canonical facts about artmind's fixed structural graph shape.

This is the ONE place these facts are written down as data rather than prose,
following the precedent `test_cli_guide.py` already sets for `COMMAND_GROUPS`
(CLAUDE.md's testing implication #4 — "docs and code drift in both
directions"). Two consumers read it instead of re-stating it by hand:

- `artmind.text2cypher.STRUCTURAL_SCHEMA` — the block embedded in every
  text2cypher prompt — is rendered from this module (`render_prompt_block()`),
  so a structural change (a new relationship, a renamed property) has to be
  made here to reach the model at all.
- `test/test_query_skill_structural_schema.py` reads
  `artmind/skills/artmind-query/SKILL.md`'s own "Fixed Structural Schema"
  section and asserts it names every label/relationship declared here, and
  none of `RETIRED_NAMES` — the guard against exactly the kind of drift that
  left a dead `(:UserChat)-[:MENTIONS]->(:Entity)` line in that skill file
  through three redesign phases.

Keep this module in sync with the graph itself by hand — there is no
introspection step (a live schema scan can't tell you *why* two labels are
mutually exclusive, or that an Entity's `_id` prefix is deliberate). What it
buys instead is a single edit site for the two things that read it.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class NodeLabel:
    name: str
    properties: tuple[str, ...]
    # Optional explanatory lines, already wrapped/indented as they should
    # appear under the node's own line in the rendered prompt block.
    note_lines: tuple[str, ...] = ()


@dataclass(frozen=True)
class Relationship:
    rel_type: str
    from_label: str
    to_label: str
    properties: tuple[str, ...] = ()
    inline_note: str = ""
    note_lines: tuple[str, ...] = ()


NODES: tuple[NodeLabel, ...] = (
    NodeLabel(
        "Document",
        ("id", "name", "path", "_domain", "valid_from", "valid_to", "superseded_by"),
    ),
    NodeLabel(
        "DocChunk",
        ("id", "name", "doc_id", "text", "_domain", "embedding"),
    ),
    NodeLabel(
        "UserChat",
        ("id", "raw_text", "_domain", "session_id", "created_by", "created_at", "embedding"),
    ),
    NodeLabel(
        "Observation",
        (
            "id", "name", "canonical_name", "key", "entity_class", "_domain", "doc_id",
            "chunk_id", "_valid_from", "_valid_to", "_doc_valid_from", "_kind",
        ),
        note_lines=(
            "what one chunk of one document version asserted. Immutable, carries NO :Entity label",
            "and no class label, so it never appears in an entity query by accident. Provenance",
            'only — never the answer to "what is true now"; that is :Entity, below.',
        ),
    ),
    NodeLabel(
        "Entity",
        ("_id", "name", "entity_class", "_domain", "description", "type"),
        note_lines=(
            "the projection: one node per real-world thing, current by construction, rebuilt from",
            "observations. NOTE the leading underscore on _id/_domain — EVERY label in this graph",
            "uses _domain (artmind-computed: assigned by --domain/vault.yaml/frontmatter, never",
            "something the LLM extracts), but _id is Entity-only; every other label's own identity",
            "property is plain unprefixed id.",
        ),
    ),
    NodeLabel(
        "Conflict",
        (
            "id", "_source", "status",
            # adjudicator shape (_source='adjudicator'): a pairwise, cross-entity
            # disagreement, found by the same-as candidate LLM adjudicator
            "aspect", "claim_a", "claim_b", "severity", "domains", "detected_at", "detected_by_model",
            # projection shape (_source='projection'): a single entity's own
            # property disputed within one instant, found by the rebuild itself
            "property", "entity_id", "_domain", "kind", "values", "detected_by",
        ),
        note_lines=(
            "two shapes sharing one label, told apart by `_source`. 'adjudicator' is a",
            "cross-entity disagreement (aspect/claim_a/claim_b/severity, CONFLICTS_WITH between",
            "the two entities, EVIDENCE -> DocChunk) — `query graph conflicts` reaches only this",
            "shape. 'projection' is one entity's own property disputed within a single instant",
            "(property/entity_id/values, EVIDENCE -> Observation, no CONFLICTS_WITH edge at",
            "all) — raised by the rebuild itself, not a detection pass, and has no dedicated",
            "query command yet; reach it via entity-history (same valid_from, different values)",
            "or a hand-written Cypher match on (:Conflict {_source: 'projection'}).",
        ),
    ),
)

RELATIONSHIPS: tuple[Relationship, ...] = (
    Relationship("PART_OF", "DocChunk", "Document", inline_note="chunk belongs to a document"),
    Relationship("EXTRACTED_FROM", "Observation", "DocChunk", inline_note="observation's source chunk"),
    Relationship(
        "AGGREGATES", "Entity", "Observation",
        inline_note="entity's current observations",
        note_lines=(
            "(to find which chunks/documents mention an entity, go (:Entity)-[:AGGREGATES]->",
            "(:Observation)-[:EXTRACTED_FROM]->(:DocChunk) — an Entity never has a direct",
            "EXTRACTED_FROM edge)",
        ),
    ),
    Relationship(
        "ASSERTS_RELATION", "Observation", "Observation",
        ("rel_type", "doc_id", "chunk_id"),
        note_lines=(
            "the raw, chunk-scoped record of one extracted relationship. Provenance only; for",
            '"what relationships does this entity have", use RELATES_TO below instead.',
        ),
    ),
    Relationship(
        "RELATES_TO", "Entity", "Entity",
        ("rel_type", "observation_count", "chunk_ids", "doc_ids"),
        note_lines=(
            "EVERY entity-to-entity relationship uses this ONE type, whatever its real-world",
            "meaning (owns, regulates, part_of, ...). The meaning is `rel_type`, a PROPERTY, not",
            "the Neo4j type — filter with `WHERE r.rel_type = '...'`, never `-[:SOME_MEANING]->`.",
            "This is deliberate (Phase 4 collapsed 249 per-domain types into this one): do not",
            "invent a relationship type matching the question's verb.",
        ),
    ),
    Relationship(
        "CONFLICT_OF", "Conflict", "Entity",
        inline_note="one entity (projection shape) or both sides (adjudicator shape)",
    ),
    Relationship(
        "EVIDENCE", "Conflict", "DocChunk", ("side",),
        inline_note="adjudicator shape only: competing claim text",
    ),
    Relationship(
        "EVIDENCE", "Conflict", "Observation",
        inline_note="projection shape only: the disputed observations themselves",
    ),
    Relationship("CONFLICTS_WITH", "Entity", "Entity", ("conflict_id", "aspect"),
                 inline_note="adjudicator shape only — never written by the projection rebuild"),
    Relationship("SUPERSEDES", "Document", "Document", ("scope", "effective"), inline_note="newer replaces older"),
)

# (base label, history label) — mutually exclusive, same properties, the
# retired counterpart of the base label. See projection-pipeline.md's Phase 4
# notes ("Label swaps") for why this is a label pair and not a status property.
HISTORY_LABEL_PAIRS: tuple[tuple[str, str], ...] = (
    ("Document", "DocumentHistory"),
    ("DocChunk", "DocChunkHistory"),
    ("Observation", "ObservationHistory"),
)

# Names that belonged to a pre-redesign model and must not appear in generated
# prose or in the artmind-query skill's own documentation, full stop — not even
# as a "this used to exist" footnote. There is no backward compatibility and
# the corpus is re-ingested from scratch at Phase 8 (redesign-phase-plan.md),
# so explaining a dead mechanism is dead weight, not a kindness: it spends
# words on a thing the reader will never encounter. Extend this set the next
# time a removal produces a new dead name to guard against.
RETIRED_NAMES: tuple[str, ...] = (
    "MENTIONS",
    "event_at",
    "entity-versions",
    "_is_current",
)


def _props_suffix(properties: tuple[str, ...]) -> str:
    return f" {{{', '.join(properties)}}}" if properties else ""


def render_prompt_block() -> str:
    """Render the STRUCTURAL GRAPH block embedded in the text2cypher prompt.

    Content, not just labels: every node's properties, every relationship's
    endpoints and properties, and the explanatory notes that keep an LLM from
    inventing a relationship type or traversing into history by accident.
    """
    lines = ["STRUCTURAL GRAPH (fixed for all domains — use these exact relationship names):"]

    for node in NODES:
        lines.append(f"  Node :{node.name}  properties={list(node.properties)}")
        for i, note_line in enumerate(node.note_lines):
            lines.append(f"    — {note_line}" if i == 0 else f"      {note_line}")

    for rel in RELATIONSHIPS:
        pattern = f"(:{rel.from_label})-[:{rel.rel_type}{_props_suffix(rel.properties)}]->(:{rel.to_label})"
        head = f"  Relationship {pattern}"
        if rel.inline_note:
            head += f"  — {rel.inline_note}"
        lines.append(head)
        for note_line in rel.note_lines:
            lines.append(f"    {note_line}")

    lines.append("  History labels " + " / ".join(f":{h}" for _, h in HISTORY_LABEL_PAIRS)
                 + " — the retired")
    lines.append(
        "    counterpart of " + " / ".join(f":{b}" for b, _ in HISTORY_LABEL_PAIRS)
        + " (same properties, mutually"
    )
    lines.append(
        "    exclusive with the base label). Never traverse into these for a \"what is true now\""
    )
    lines.append(
        "    question — the base labels are current by construction. Only match a History label"
    )
    lines.append("    when the question is explicitly about retired/superseded/historical content.")
    lines.append("  Timed nodes carry valid_from/valid_to; superseded docs also carry superseded_by.")

    return "\n".join(lines)


def canonical_names() -> tuple[str, ...]:
    """Every label/relationship-type name a correct description of the graph must use.

    What `test_query_skill_structural_schema.py` checks the skill's prose
    against — not phrase-for-phrase, just "does every one of these appear".
    """
    names = [n.name for n in NODES]
    names += [r.rel_type for r in RELATIONSHIPS]
    names += [h for pair in HISTORY_LABEL_PAIRS for h in pair]
    return tuple(dict.fromkeys(names))
