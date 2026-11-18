"""The one-time OBJECT -> ACTIVITY move (spec 2026-11-15 §5.3; plan question 14).

Before this model the journal schema had no occasion class for travel, so flights,
cab rides and trips were extracted as OBJECT. New extractions are kept right by
OBJECT's new description; what is already staged is moved here, once, by a word
list that lives only in the pack (no schema keyword). Pure: no I/O.

A word matches whole, singular or plural ("Flights to Goa", "Rides"), any case. Known
limit: a name that uses a travel word for something else moves too ("Cab Sauvignon",
"toy train set"); the effect reports counts only (names are private corpus data), so
review ACTIVITY names with `artmind query graph entity-listing --domain <d>` after the run
if the domain may hold such names.
"""
from __future__ import annotations

import re
from collections import Counter

TRAVEL_WORDS = ("flight", "train", "cab", "taxi", "ride", "trip", "journey", "booking")

_WORDS = re.compile(r"\b(?:" + "|".join(TRAVEL_WORDS) + r")s?\b", re.IGNORECASE)
SOURCE, TARGET = "OBJECT", "ACTIVITY"


def applies(schema: dict) -> bool:
    """Only a schema that declares both classes (personal_journal and its children)."""
    types = schema.get("entity_types") if isinstance(schema, dict) else None
    return isinstance(types, dict) and SOURCE in types and TARGET in types


def is_travel(name) -> bool:
    """A travel word, whole word, any case, in the observation's canonical name."""
    return isinstance(name, str) and _WORDS.search(name) is not None


def reclassify(observations: list[dict], schema: dict) -> tuple[list[dict], Counter]:
    """(`observations` with every travel OBJECT made an ACTIVITY, {"OBJECT->ACTIVITY": n}).

    The moved observation takes ACTIVITY's `_kind` from the schema and loses `type`
    (OBJECT's type values mean nothing to ACTIVITY). Its `key` is left for core
    `retime.retime_observations` to rebuild; its id never changes. A table row keeps
    its mapping's class."""
    from artmind.observations import class_kind

    moved: Counter = Counter()
    if not applies(schema):
        return list(observations), moved
    out = []
    for obs in observations:
        if (isinstance(obs, dict) and obs.get("entity_class") == SOURCE and obs.get("source_kind") != "table"
                and is_travel(obs.get("canonical_name"))):
            obs = {k: v for k, v in obs.items() if k != "type"}
            obs["entity_class"] = TARGET
            obs["_kind"] = class_kind(schema, TARGET)
            moved[f"{SOURCE}->{TARGET}"] += 1
        out.append(obs)
    return out, moved
