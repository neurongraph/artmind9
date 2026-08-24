# artmind/update.py
import json
import uuid
from datetime import date, datetime
from pathlib import Path

import yaml
from loguru import logger

from artmind.db import (
    _create_update_draft,
    _create_update_session,
    _get_latest_pending_draft,
    _get_update_session,
    _list_update_sessions,
    _update_draft_status,
    _update_session_status,
)
from artmind.extraction import (
    build_entities_prompt,
    build_properties_prompt,
    build_relationships_prompt,
    embed_text,
    extract_with_retry,
)
from artmind.graph_query import neo4j_session
from artmind.ingest import (
    RESERVED_REL_TYPES,
    _flatten_props,
    _sanitize_label,
    embed_missing_entity_embeddings,
)
from paths import DOMAIN_SCHEMAS_DIR
from utils.functions import load_env, resolve_llm_model


def _classify_input(text: str) -> str:
    text = text.strip()
    if len(text) > 500:
        return "bulk"
    lower = text.lower()
    sentences = [s.strip() for s in text.split(".") if s.strip()]
    if len(sentences) <= 1:
        if any(kw in lower for kw in ("todo", "task", "remind", "need to", "should")):
            return "todo"
        return "atomic_fact"
    return "passage"


def extract_facts(
    text: str, domain: str, schema: dict, text_model: str | None = None
) -> dict:
    env = load_env()
    model = resolve_llm_model(env, text_model)

    raw_entities, ok = extract_with_retry(
        "update_entities", model, build_entities_prompt(text, schema)
    )
    if not ok:
        raw_entities = []

    entities = [
        {
            "temp_id": e.get("id", f"e{i}"),
            "name": e.get("name", ""),
            "entity_class": e.get("entity_class", "UNKNOWN"),
            "properties": {},
        }
        for i, e in enumerate(raw_entities)
    ]

    raw_props: list = []
    raw_rels: list = []
    if raw_entities:
        raw_props, _ = extract_with_retry(
            "update_properties",
            model,
            build_properties_prompt(text, raw_entities, schema),
        )
        raw_rels, _ = extract_with_retry(
            "update_relationships",
            model,
            build_relationships_prompt(text, raw_entities, schema),
        )

    props_by_name = {
        p.get("name", p.get("id", "")): p.get("properties", {})
        for p in raw_props
    }
    for entity in entities:
        entity["properties"] = props_by_name.get(entity["name"], {})

    name_to_temp = {e["name"]: e["temp_id"] for e in entities}
    relationships = [
        {
            "source_temp_id": name_to_temp[r["source_name"]],
            "target_temp_id": name_to_temp[r["target_name"]],
            "rel_type": r.get("rel_type", "RELATED_TO"),
            "description": r.get("description", ""),
        }
        for r in raw_rels
        if r.get("source_name") in name_to_temp and r.get("target_name") in name_to_temp
    ]

    return {"entities": entities, "relationships": relationships}


def find_candidates(
    entity_name: str, entity_class: str, domain: str, top_n: int = 5
) -> list[dict]:
    # Lucene fulltext scores are unbounded (often 4-8), so exact matches must be
    # ranked via a separate flag rather than a remapped score, or they lose to
    # fuzzy hits. Same-class candidates rank next: `link` resolves by
    # name + entity_class, so cross-class candidates are rarely the right target.
    cypher_domain = """
    CALL db.index.fulltext.queryNodes('entity_name_ft', $name)
    YIELD node AS e, score AS ftScore
    WHERE (e.domain = $domain OR e.domain STARTS WITH ($domain + '.'))
    RETURN elementId(e) AS node_id, e.name AS name, e.entity_class AS entity_class,
           e.description AS context_snippet, ftScore AS match_score,
           (toLower(e.name) = toLower($name)) AS is_exact
    ORDER BY is_exact DESC, (e.entity_class = $entity_class) DESC,
             match_score DESC, size(e.name) ASC
    LIMIT $top_n
    """
    cypher_global = """
    CALL db.index.fulltext.queryNodes('entity_name_ft', $name)
    YIELD node AS e, score AS ftScore
    RETURN elementId(e) AS node_id, e.name AS name, e.entity_class AS entity_class,
           e.description AS context_snippet, ftScore AS match_score,
           (toLower(e.name) = toLower($name)) AS is_exact
    ORDER BY is_exact DESC, (e.entity_class = $entity_class) DESC,
             match_score DESC, size(e.name) ASC
    LIMIT $top_n
    """
    with neo4j_session() as session:
        rows = session.run(
            cypher_domain, domain=domain, name=entity_name,
            entity_class=entity_class, top_n=top_n,
        ).data()
        if not rows:
            rows = session.run(
                cypher_global, name=entity_name,
                entity_class=entity_class, top_n=top_n,
            ).data()
    return rows


def find_supersession_candidates(
    source_node_id: str, rel_type: str, target_name: str, top_n: int = 3
) -> list[dict]:
    """Existing (source)-[rel_type]->(other) edges to a DIFFERENT target than the
    one just extracted — a signal the new fact replaces the old one rather than
    adding beside it (e.g. a branch's headed_by changing from one manager to
    another). Matched by elementId since `source_node_id` comes from
    find_candidates. Purely a suggestion surfaced to the user; never applied
    automatically.
    """
    sanitized_type = _sanitize_label(rel_type)
    cypher = """
    MATCH (s:Entity) WHERE elementId(s) = $sourceNodeId
    MATCH (s)-[r]->(existing:Entity)
    WHERE type(r) = $relType AND toLower(existing.name) <> toLower($targetName)
    RETURN DISTINCT elementId(existing) AS node_id, existing.name AS name,
           existing.entity_class AS entity_class, type(r) AS rel_type
    LIMIT $top_n
    """
    with neo4j_session() as session:
        rows = session.run(
            cypher, sourceNodeId=source_node_id, relType=sanitized_type,
            targetName=target_name, top_n=top_n,
        ).data()
    return rows


def _load_schema(domain: str) -> dict:
    schema_file = DOMAIN_SCHEMAS_DIR / f"{domain}_schema.yaml"
    if not schema_file.exists():
        schema_file = DOMAIN_SCHEMAS_DIR / "general_schema.yaml"
    return yaml.safe_load(schema_file.read_text(encoding="utf-8"))


def _ensure_user_chat_schema(session, embedding_dim: int = 768) -> None:
    session.run(
        "CREATE CONSTRAINT user_chat_id IF NOT EXISTS FOR (n:UserChat) REQUIRE n.id IS UNIQUE"
    )
    session.run(
        f"CREATE VECTOR INDEX user_chat_embedding IF NOT EXISTS "
        f"FOR (c:UserChat) ON (c.embedding) "
        f"OPTIONS {{indexConfig: {{`vector.dimensions`: {embedding_dim}, "
        f"`vector.similarity_function`: 'cosine'}}}}"
    )


def _find_existing_entity(
    session, name: str, entity_class: str, domain: str
) -> dict | None:
    """The projected entity already holding this identity, if any."""
    from artmind.observations import aggregate_key, entity_id

    eid = entity_id(aggregate_key(name, entity_class, domain))
    rec = session.run(
        "MATCH (e:Entity {id: $id}) RETURN e.id AS id, e.name AS name LIMIT 1", id=eid
    ).single()
    return {"id": rec["id"], "name": rec["name"]} if rec else None


def _resolve_target_identity(
    session, node_ref: str | None, name: str, entity_class: str, domain: str
) -> dict | None:
    """The identity a resolution should be recorded AGAINST — not a write.

    This is the fix for the defect `CLAUDE.md` describes. `update confirm` used
    to patch entity properties matched by the **LLM's extracted name**, so a
    user who picked "Alice Smith" for an extracted "Alice" silently updated
    nothing while the returned counts still reported success.

    Under the observation model the fix is structural rather than careful: the
    write is no longer "find the Entity and patch it". We look up the node the
    user actually chose, take *its* name/class/domain, and record the
    observation under that canonical name. The aggregate key then lands the
    observation on that same entity by construction — there is no separate
    matching step left to get wrong.

    `node_ref` is whichever identifier the caller has in hand: the elementId
    `find_candidates` returns, or the app-managed `id` property
    `entity_context`/`pattern2` return.
    """
    if node_ref:
        rec = session.run(
            """
            MATCH (e:Entity) WHERE elementId(e) = $ref OR e.id = $ref
            RETURN e.id AS id, e.name AS name, e.entity_class AS entity_class,
                   e.domain AS domain
            LIMIT 1
            """,
            ref=node_ref,
        ).single()
        if rec:
            return {
                "id": rec["id"],
                "name": rec["name"],
                "entity_class": rec["entity_class"] or entity_class,
                "domain": rec["domain"] or domain,
            }
        return None

    existing = _find_existing_entity(session, name, entity_class, domain)
    if existing:
        return {**existing, "entity_class": entity_class, "domain": domain}
    return None


def write_user_chat(
    session_id: str,
    raw_text: str,
    domain: str,
    user_id: str,
    resolutions: list[dict],
    extracted_entities: list[dict],
    extracted_relationships: list[dict],
) -> dict:
    """Record a UserChat and the Observations it asserts, then rebuild.

    A conversational correction is a **source**, exactly like a document, and
    it earns its authority the same way every other source does: its
    observations carry today's date as their document-level `valid_from`, so
    the projection's winner rule prefers them over an older schedule without
    any special-casing. Nothing here writes to an `:Entity` directly — the
    projection owns every entity property, and a direct write would be silently
    reverted by the next rebuild.

    Entity-level supersession is gone (`apply_node_supersession`, along with
    `Entity.superseded_by` and `status='superseded'`). Those were projection-
    owned properties, so a chat that set them would have them wiped by the next
    rebuild. A `supersedes` resolution is reported rather than applied — see
    the warning below for what to use instead.
    """
    from artmind import projection
    from artmind.observations import aggregate_key, build_observation, key_string
    from artmind.temporal import load_schema

    env = load_env()
    embed_model = env.get("ARTMIND_KG_EMBEDDINGS_MODEL", "nomic-embed-text:latest")
    embedding_dim = int(env.get("ARTMIND_KG_EMBEDDING_DIMENSIONS", "768"))
    now = datetime.now().isoformat()
    today = date.today().isoformat()
    chat_id = uuid.uuid4().hex
    embedding = embed_text(embed_model, raw_text)
    input_hint = _classify_input(raw_text)
    schema = load_schema(domain)

    observations: list[dict] = []
    resolved: dict[str, dict] = {}
    nodes_created = 0
    nodes_updated = 0
    nodes_superseded = 0

    with neo4j_session() as session:
        _ensure_user_chat_schema(session, embedding_dim)

        session.run(
            """
            CREATE (c:UserChat {
                id: $id, raw_text: $raw_text, embedding: $embedding,
                domain: $domain, session_id: $session_id,
                input_hint: $input_hint, created_at: $now, created_by: $user_id,
                _status: 'latest', _valid_from: $today
            })
            """,
            id=chat_id, raw_text=raw_text, embedding=embedding,
            domain=domain, session_id=session_id, today=today,
            input_hint=input_hint, now=now, user_id=user_id,
        )

        for res in resolutions:
            temp_id = res["entity_temp_id"]
            action = res["action"]
            entity_data = next(
                (e for e in extracted_entities if e["temp_id"] == temp_id), None
            )
            if not entity_data:
                continue

            entity_class = entity_data["entity_class"]
            canonical_name = entity_data["name"]
            target_domain = domain

            if action == "link":
                target = _resolve_target_identity(
                    session, res.get("node_id"), entity_data["name"], entity_class, domain
                )
                if not target:
                    # Counting a link that matched nothing would report a write
                    # that never happened.
                    logger.warning(
                        "link resolution for {!r} ({}) matched no node "
                        "(node_id={!r}, domain={!r}); skipped",
                        entity_data["name"], entity_class, res.get("node_id"), domain,
                    )
                    continue
                # The chosen node's identity, NOT the extracted surface form.
                canonical_name = target["name"]
                entity_class = target["entity_class"]
                target_domain = target["domain"]
                nodes_updated += 1
            elif action == "create":
                if _find_existing_entity(session, canonical_name, entity_class, domain):
                    nodes_updated += 1
                else:
                    nodes_created += 1
            else:
                continue

            key = aggregate_key(canonical_name, entity_class, target_domain)
            observation = build_observation(
                {
                    "name": entity_data["name"],
                    "entity_class": entity_class,
                    "domain": target_domain,
                    "type": entity_data.get("type"),
                    "description": entity_data.get("description"),
                    "context": entity_data.get("context"),
                    "aliases": entity_data.get("aliases"),
                },
                canonical_name=canonical_name,
                domain_props=entity_data.get("properties", {}),
                doc_id=chat_id,
                doc_version=1,
                chunk_id=chat_id,
                kind=_class_kind(schema, entity_class),
                doc_valid_from=today,
                valid_time_source="user_chat",
            )
            observation["source_kind"] = "user_chat"
            observation["created_by"] = user_id
            observations.append(observation)
            resolved[temp_id] = {"id": _entity_id_for(key), "name": canonical_name}

        for res in resolutions:
            if res.get("supersedes"):
                nodes_superseded += 0
                logger.warning(
                    "supersession in chat {} not applied: entity-level supersession was "
                    "removed with the projection (Entity.superseded_by is derived now). "
                    "Retire the source document (`docs retire`) or declare a same-as "
                    "group instead.", chat_id,
                )

        # One transaction: the observations and the rebuild they dirty.
        def _write(tx):
            for observation in observations:
                tx.run(
                    "MERGE (o:Observation {id: $id}) SET o = $props",
                    id=observation["id"], props=observation,
                )
                tx.run(
                    """
                    MATCH (o:Observation {id: $id})
                    MATCH (c:UserChat {id: $chat_id})
                    MERGE (o)-[:EXTRACTED_FROM]->(c)
                    """,
                    id=observation["id"], chat_id=chat_id,
                )
            keys = {
                tuple(o["key"].split("|")) for o in observations
                if o.get("key") and o["key"].count("|") == 2
            }
            return projection.rebuild(tx, keys)

        session.execute_write(_write)

        rel_count = _write_chat_relationships(
            session, extracted_relationships, resolved, chat_id, now, user_id
        )

        embed_missing_entity_embeddings(session, domain, embed_model)

    return {
        "user_chat_id": chat_id,
        "nodes_created": nodes_created,
        "nodes_updated": nodes_updated,
        "nodes_superseded": nodes_superseded,
        "observations_written": len(observations),
        "relationships_written": rel_count,
    }


def _entity_id_for(key) -> str:
    from artmind.observations import entity_id

    return entity_id(key)


def _class_kind(schema: dict, entity_class: str) -> str:
    from artmind.observations import class_kind

    return class_kind(schema, entity_class)


def _write_chat_relationships(
    session, extracted_relationships, resolved, chat_id, now, user_id
) -> int:
    """Entity->Entity edges asserted by a chat, matched by the resolved entity
    ids the projection just produced."""
    rel_count = 0
    for rel in extracted_relationships:
        src = resolved.get(rel.get("source_temp_id", ""))
        tgt = resolved.get(rel.get("target_temp_id", ""))
        if not src or not tgt or src["id"] == tgt["id"]:
            continue
        rel_type = _sanitize_label(rel.get("rel_type", "RELATED_TO"))
        if rel_type in RESERVED_REL_TYPES:
            logger.warning(
                "Reserved relationship type skipped ({} -[{}]-> {}); "
                "only audited helpers may create this edge type",
                src["name"], rel_type, tgt["name"],
            )
            continue
        rel_props = _flatten_props({
            "source_chat_id": chat_id,
            "created_at": now,
            "created_by": user_id,
            "updated_at": now,
            "updated_by": user_id,
        })
        try:
            written = session.run(
                """
                MATCH (src:Entity {id: $srcId})
                MATCH (tgt:Entity {id: $tgtId})
                CALL apoc.merge.relationship(src, $type, {source_chat_id: $chat_id},
                     $props, tgt, {}) YIELD rel
                RETURN rel
                """,
                srcId=src["id"], tgtId=tgt["id"], type=rel_type,
                chat_id=chat_id, props=rel_props,
            ).single()
            if written:
                rel_count += 1
            else:
                logger.warning(
                    "Relationship not written ({} -[{}]-> {}): endpoints did not match",
                    src["name"], rel_type, tgt["name"],
                )
        except Exception as e:
            logger.warning(
                "Relationship skipped ({} -[{}]-> {}): {}",
                src["name"], rel_type, tgt["name"], e,
            )
    return rel_count


def _detect_supersession_candidates(
    entities: list[dict], relationships: list[dict], candidates_per_entity: list[dict]
) -> list[dict]:
    """Flag extracted relationships whose source already has a same-rel_type
    edge to a DIFFERENT target — e.g. a branch's headed_by changing from one
    manager to another. Only triggered when the source resolves to an exact
    existing-node match (a fuzzy/no match means there's no prior fact yet to
    replace). Purely a suggestion for the caller to confirm with the user;
    resolution into an actual `supersedes` action happens at confirm time.
    """
    name_by_temp = {e["temp_id"]: e["name"] for e in entities}
    candidates_by_temp = {c["temp_id"]: c["top_n"] for c in candidates_per_entity}

    detected = []
    for rel in relationships:
        source_hits = candidates_by_temp.get(rel["source_temp_id"], [])
        if not source_hits or not source_hits[0].get("is_exact"):
            continue
        target_name = name_by_temp.get(rel["target_temp_id"], "")
        if not target_name:
            continue
        replaces = find_supersession_candidates(
            source_hits[0]["node_id"], rel["rel_type"], target_name,
        )
        if replaces:
            detected.append({
                "source_temp_id": rel["source_temp_id"],
                "source_name": source_hits[0]["name"],
                "target_temp_id": rel["target_temp_id"],
                "new_target_name": target_name,
                "rel_type": rel["rel_type"],
                "replaces": replaces,
            })
    return detected


def draft_update(
    domain: str, text: str, session_id: str | None, user_id: str
) -> dict:
    if session_id:
        # confirm_update writes with the *session's* domain (joined from
        # update_sessions), not the one passed here, so a resumed turn given a
        # different --domain would extract and offer candidates from one domain
        # and write to another. Rejected up front rather than half-applied.
        # An unknown session id is caught here too: FKs are never enforced in
        # this DB, so the draft would insert cleanly and only fail at confirm
        # with a misleading "no pending draft".
        existing = _get_update_session(session_id)
        if not existing:
            raise ValueError(f"No such update session: {session_id!r}")
        if existing["domain"] != domain:
            raise ValueError(
                f"Session {session_id!r} belongs to domain {existing['domain']!r}, "
                f"not {domain!r} — confirm writes with the session's domain. "
                f"Start a new session for a different domain."
            )
    else:
        session_id = uuid.uuid4().hex
        _create_update_session(session_id, domain, user_id)

    schema = _load_schema(domain)

    facts = extract_facts(text, domain, schema)

    candidates_per_entity = [
        {
            "entity": e["name"],
            "temp_id": e["temp_id"],
            "top_n": find_candidates(e["name"], e["entity_class"], domain),
        }
        for e in facts["entities"]
    ]

    supersession_candidates = _detect_supersession_candidates(
        facts["entities"], facts["relationships"], candidates_per_entity
    )

    _create_update_draft(
        session_id=session_id,
        raw_text=text,
        input_hint=_classify_input(text),
        extraction_json=json.dumps(facts),
        candidates_json=json.dumps(candidates_per_entity),
    )

    return {
        "session_id": session_id,
        "extracted_entities": facts["entities"],
        "extracted_relationships": facts["relationships"],
        "candidates_per_entity": candidates_per_entity,
        "supersession_candidates": supersession_candidates,
    }


def confirm_update(session_id: str, resolutions: list[dict], user_id: str) -> dict:
    draft = _get_latest_pending_draft(session_id)
    if not draft:
        raise ValueError(f"No pending draft for session {session_id!r}")

    facts = json.loads(draft["extraction_json"])

    result = write_user_chat(
        session_id=session_id,
        raw_text=draft["raw_text"],
        domain=draft["domain"],
        user_id=user_id,
        resolutions=resolutions,
        extracted_entities=facts["entities"],
        extracted_relationships=facts["relationships"],
    )

    _update_draft_status(draft["id"], "confirmed")
    _update_session_status(session_id, "confirmed")

    return result


def export_chats(
    domain: str | None, format: str, output_dir: Path
) -> list[Path]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    if format == "sequential":
        # Hierarchical rollup, matching graph_query.domain_predicate and
        # find_candidates above: a parent-domain filter includes descendants.
        cypher = """
        MATCH (c:UserChat)
        WHERE $domain IS NULL OR c.domain = $domain
           OR c.domain STARTS WITH ($domain + '.')
        OPTIONAL MATCH (c)-[:MENTIONS]->(e:Entity)
        WITH c, collect(e.name) AS mentions
        ORDER BY c.created_at ASC
        RETURN c.session_id AS session_id, c.id AS id, c.raw_text AS raw_text,
               c.domain AS domain, c.created_by AS created_by,
               c.created_at AS created_at, c.input_hint AS input_hint,
               mentions
        """
        with neo4j_session() as session:
            rows = session.run(cypher, domain=domain).data()

        sessions: dict[str, list[dict]] = {}
        for row in rows:
            sessions.setdefault(row["session_id"], []).append(row)

        for sid, chats in sessions.items():
            lines = [f"# Session {sid}\n"]
            for chat in chats:
                lines.append(f"**{chat['created_at']}** — {chat['created_by']}")
                lines.append(f"*Domain:* {chat['domain']}  *Hint:* {chat['input_hint']}")
                lines.append(f"\n{chat['raw_text']}\n")
                if chat["mentions"]:
                    lines.append(f"*Mentions:* {', '.join(chat['mentions'])}\n")
                lines.append("---\n")
            out = output_dir / f"session_{sid[:8]}.md"
            out.write_text("\n".join(lines), encoding="utf-8")
            written.append(out)

    elif format == "by-entity":
        cypher = """
        MATCH (c:UserChat)-[:MENTIONS]->(e:Entity)
        WHERE $domain IS NULL OR c.domain = $domain
           OR c.domain STARTS WITH ($domain + '.')
        WITH e.name AS entity_name, collect({
            id: c.id, raw_text: c.raw_text, created_by: c.created_by,
            created_at: c.created_at, domain: c.domain
        }) AS chats
        ORDER BY entity_name
        RETURN entity_name, chats
        """
        with neo4j_session() as session:
            rows = session.run(cypher, domain=domain).data()

        for row in rows:
            entity_name = row["entity_name"]
            safe_name = "".join(c if c.isalnum() else "_" for c in entity_name)
            lines = [f"# {entity_name}\n"]
            for chat in row["chats"]:
                lines.append(f"**{chat['created_at']}** — {chat['created_by']}")
                lines.append(f"\n{chat['raw_text']}\n")
                lines.append("---\n")
            out = output_dir / f"entity_{safe_name}.md"
            out.write_text("\n".join(lines), encoding="utf-8")
            written.append(out)

    return written
