"""`mart build` (spec §8): graph + records → tables → storage + metadata. No LLM."""
from __future__ import annotations

from artmind.mart import graph as mart_graph
from artmind.mart import metadata, records, storage, tables


def _generation(domains) -> int:
    from artmind.catalogue.state import generations
    from artmind.graph_query import read_session

    with read_session() as session:
        return sum(generations(session, list(domains)).values())


def assemble_all(decl, domains) -> dict[str, list[dict]]:
    classes = sorted(set(decl.happening_classes))
    members = {decl.person.entity_class: mart_graph.members(domains, decl.person.entity_class),
               decl.place.entity_class: mart_graph.members(domains, decl.place.entity_class)}
    happen = mart_graph.happenings(domains, classes)
    mention = mart_graph.mentions(domains, decl.mention_classes)
    bridge = mart_graph.bridges(domains, classes, [decl.person.entity_class, decl.place.entity_class])
    recs = records.load_dimensions(decl.domain)
    out = tables.assemble(decl, happen, mention, bridge, recs, records.load_periods(decl.domain), members=members)
    if decl.wholes:
        from artmind.mart import wholes

        out.update(wholes.compute(decl, out))
    return out


def build(decl, domains, *, dry_run: bool) -> dict:
    assembled = assemble_all(decl, domains)
    result = {"domain": decl.domain, "generation": _generation(domains),
              "rows": {k: len(v) for k, v in assembled.items()}}
    if dry_run:
        return result
    storage.write_tables(decl.domain, assembled)
    result["metadata"] = str(metadata.write(decl.domain, assembled))
    return result
