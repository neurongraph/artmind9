"""The entity history zone: snapshots of overwritten entity property values.

Document supersession retires entities wholesale (see temporal._retire_orphaned_entities).
This module handles the other half: when a superseding document *overwrites* an
entity's property values rather than dropping the entity, the prior values are
preserved as an :EntityVersion node so point-in-time questions stay answerable.

Snapshots deliberately carry neither the :Entity label nor a class label. Every
existing consumer — pattern1-9, entity_listing, entity-resolve, the
entity_embedding vector index, refine-graph clustering, candidate_pairs — matches
on :Entity or a class label, so none can see history without asking. That
isolation is structural, not a filter anyone has to remember.
"""
from artmind.temporal import (
    _read_doc_body,
    load_schema,
    parse_supersession_metadata_table,
    parse_supersession_notice,
)


def supersession_possible(doc_name: str, domain: str) -> bool:
    """Could supersession fire for this document? Pure local work.

    The parse step needs no graph access — both parsers are regex over markdown
    already on disk — so this runs before the (much more expensive) prior-value
    capture and skips it entirely for the overwhelming majority of documents,
    which declare no supersession at all.

    The title-family route is the one signal that lives outside the document, so
    a domain with `supersede_on_title_family` set always passes the gate. That
    flag is off by default and set only by schema authors who want version
    chains, so those domains genuinely expect supersession.
    """
    defaults = (load_schema(domain).get("temporal") or {}).get("defaults") or {}
    if defaults.get("supersede_on_title_family"):
        return True
    body = _read_doc_body(doc_name)
    if not body:
        return False
    return bool(parse_supersession_notice(body) or parse_supersession_metadata_table(body))
