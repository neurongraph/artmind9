"""Identity rules for `query entity-resolve`: first person -> narrator, same-name twins.

Reimplements, in classic code, the rules the planner applies (first-person words, name
normalisation); nothing here imports `artmind.query_plan`.
"""
import re

from artmind import domain_config

FIRST_PERSON = ("i", "me", "my", "mine", "myself")


def normalise(text) -> str:
    """Casefold, punctuation -> space, whitespace collapsed."""
    return " ".join(re.sub(r"[^\w\s]", " ", str(text or "").casefold()).split())


def first_person(reference: str) -> str:
    """'whole' when the reference is just one first-person word, 'leading' when it starts with
    one among other words, '' otherwise."""
    words = normalise(reference).split()
    if not words or words[0] not in FIRST_PERSON:
        return ""
    return "whole" if len(words) == 1 else "leading"


def narrator_for(domains: list[str]) -> tuple[dict | None, list[str]]:
    """The narrator to use for these domains and any warnings.

    Applies only when every given domain configures a narrator with the same name (so a single
    domain with one, or several agreeing). A broken config file gives a warning and no narrator."""
    found, warnings = [], []
    for domain in domains:
        try:
            found.append(domain_config.narrator(domain))
        except domain_config.DomainConfigError as exc:
            warnings.append(f"narrator config for domain '{domain}' ignored: {exc}")
            return None, warnings
    if not found or any(n is None for n in found):
        return None, warnings
    if len({normalise(n["name"]) for n in found}) != 1:
        return None, warnings
    return found[0], warnings


def narrator_names(narrator: dict) -> set[str]:
    return {normalise(n) for n in [narrator["name"], *narrator.get("aliases", [])]}


def twin_groups(rows: list[dict]) -> list[dict]:
    """Groups of >= 2 rows with the same entity_class and normalised name but different ids."""
    groups: dict[tuple, dict] = {}
    for row in rows:
        entity = row.get("entity") or {}
        key = (entity.get("entity_class"), normalise(entity.get("name")))
        if not key[1]:
            continue
        group = groups.setdefault(key, {"name": entity.get("name"), "entity_class": key[0], "ids": []})
        if entity.get("_id") not in group["ids"]:
            group["ids"].append(entity.get("_id"))
    return [g for g in groups.values() if len(g["ids"]) >= 2]


def twin_warning(group: dict) -> str:
    return (f"{len(group['ids'])} entities share the name '{group['name']}' ({group['entity_class']}): "
            "treat them as one unless the text distinguishes them")
