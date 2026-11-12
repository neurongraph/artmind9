"""Identity rules for `query entity-resolve`: first person -> narrator, same-name twins.

First-person words and name normalisation, first written for the planner (removed in 0.13).
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
    """Groups of >= 2 rows with the same entity_class and normalised name but different ids.

    Also groups same-class rows where one row's name equals another's alias (`alias_of` names
    the shared alias). Rows are never merged or reordered."""
    groups: dict[tuple, dict] = {}
    for row in rows:
        entity = row.get("entity") or {}
        key = (entity.get("entity_class"), normalise(entity.get("name")))
        if not key[1]:
            continue
        group = groups.setdefault(key, {"name": entity.get("name"), "entity_class": key[0], "ids": []})
        if entity.get("_id") not in group["ids"]:
            group["ids"].append(entity.get("_id"))
    out = [g for g in groups.values() if len(g["ids"]) >= 2]
    seen_pairs: set = set()
    entities = [r.get("entity") or {} for r in rows]
    for a in entities:
        a_name = normalise(a.get("name"))
        if not a_name:
            continue
        for b in entities:
            if b is a or b.get("_id") == a.get("_id") or b.get("entity_class") != a.get("entity_class"):
                continue
            if b.get("_id") is None or a.get("_id") is None:
                continue
            if normalise(b.get("name")) == a_name:
                continue  # same-name twins are already grouped
            for alias in b.get("aliases") or []:
                if normalise(alias) == a_name:
                    pair = (a.get("_id"), b.get("_id"))
                    if pair in seen_pairs:
                        break
                    seen_pairs.add(pair)
                    out.append({"name": a.get("name"), "entity_class": a.get("entity_class"),
                                "ids": [a.get("_id"), b.get("_id")], "alias": alias,
                                "other_name": b.get("name")})
                    break
    return out


def twin_warning(group: dict) -> str:
    if group.get("alias"):
        return (f"\"{group['name']}\" and \"{group['other_name']}\" ({group['entity_class']}) "
                f"share the name \"{group['alias']}\": treat them as one unless the text distinguishes them")
    return (f"{len(group['ids'])} entities share the name '{group['name']}' ({group['entity_class']}): "
            "treat them as one unless the text distinguishes them")
