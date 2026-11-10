"""Generated text2sql metadata (spec §9.1), in the structured-table spec's format:
relationships, column values, glossary, rules. Rewritten on each build."""
from __future__ import annotations

import os

import yaml

import paths
from artmind.mart import TABLE_PREFIX

_JOINS = [("fact_happening", "place_key", "dim_place", "place_key"),
          ("fact_mention", "place_key", "dim_place", "place_key"),
          ("fact_mention", "entity_key", "fact_happening", "entity_key"),
          ("bridge_person", "entity_key", "fact_happening", "entity_key"),
          ("bridge_person", "person_key", "dim_person", "person_key"),
          ("bridge_place", "entity_key", "fact_happening", "entity_key"),
          ("bridge_place", "place_key", "dim_place", "place_key"),
          ("fact_happening", "date", "dim_time", "date"),
          ("fact_mention", "date", "dim_time", "date"),
          ("fact_happening", "type", "dim_type", "type"),
          ("fact_whole", "entity_key", "fact_happening", "entity_key")]
_VALUE_COLUMNS = {"dim_place": ["country", "region", "city", "home_away"], "dim_person": ["group", "relation", "side"],
                  "dim_type": ["family"], "dim_time": ["period"]}
_SYNONYMS = {"family": ["relative", "relatives", "kin"], "away": ["trip", "travel", "outside home"]}


def write(domain: str, tables: dict[str, list[dict]]):
    v = lambda t: f"{domain}__{TABLE_PREFIX}{t}"  # noqa: E731
    rels = [{"from": f"{v(a)}.{ac}", "to": f"{v(b)}.{bc}", "cardinality": "many_to_one"}
            for a, ac, b, bc in _JOINS if a in tables and b in tables]
    for kind in [t[len("whole_"):] for t in tables if t.startswith("whole_")]:
        rels.append({"from": f"{v('fact_whole')}.whole_id", "to": f"{v('whole_' + kind)}.whole_id",
                     "cardinality": "many_to_one"})
    out_tables = {}
    for t, cols in _VALUE_COLUMNS.items():
        if t not in tables:
            continue
        columns = {}
        for c in cols:
            values = sorted({str(r[c]) for r in tables[t] if r.get(c)})
            columns[c] = {"values": {x: {"label": x, "synonyms": _SYNONYMS.get(x, [])} for x in values}}
        out_tables[v(t)] = {"columns": columns}
    data = {
        "generated": "mart", "domain": domain, "relationships": rels, "tables": out_tables,
        "glossary": {
            "happening": f"one thing that happened: a row of {v('fact_happening')}",
            "mention": f"one dated mention of a thing: a row of {v('fact_mention')}; count these for trends",
            **{f"{k[len('whole_'):]}": f"a row of {v(k)}; its parts are in {v('fact_whole')}"
               for k in tables if k.startswith("whole_")}},
        "rules": ["Always select the evidence columns (observation_ids, chunk_ids, doc_ids, or observation_id, "
                  "chunk_id, doc_id) so every answer can be cited.",
                  f"Count happenings in {v('fact_happening')}; count how often or per period in {v('fact_mention')}."]}
    directory = paths.DOMAIN_METADATA_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{domain}.mart.meta.yaml"
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    os.replace(tmp, path)
    return path
