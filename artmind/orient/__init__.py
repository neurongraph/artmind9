"""`artmind query orient`: a layered, question-free description of the corpus (spec 2026-11-09).

`orient()` is peel 1 (corpus + one card per domain); `orient(domains)` is peel 2 (one block per
domain and its dotted children). Facts only: no question input, no route, no LLM call.
"""
from __future__ import annotations

from loguru import logger

from artmind import domain_config
from artmind.orient import budget, card, domain, schema_facts

PARTS = ("classes", "shapes", "types", "documents", "versions", "stores", "time", "assets", "health")
NEXT_PEEL = "artmind query orient --domain <domain> --compact"


def _known_domains() -> list:
    """Domains with a schema here, a structured table or graph data. `general` (always in the schema
    list) only when it holds graph data."""
    from artmind import domain_guard

    names = set(domain_guard._schema_domains())
    try:
        names |= domain_guard._table_domains()
    except Exception as exc:
        logger.debug("orient: no table domains: {}", exc)
    try:
        graph = domain_guard._graph_domains()
    except Exception as exc:
        logger.debug("orient: no graph domains: {}", exc)
        graph = set()
    names |= graph
    if "general" not in graph:
        names.discard("general")
    return sorted(names)


def _catalogues(domains):
    from artmind.catalogue import cache

    cats, _ = cache.get_detailed(domains, PARTS)
    return cats


def _facts(d: str, warnings: list):
    try:
        return schema_facts.read(d)
    except schema_facts.SchemaFactsError as exc:
        warnings.append(str(exc))
        return None


def _narrator(d: str, warnings: list):
    try:
        return domain_config.narrator(d)
    except domain_config.DomainConfigError as exc:
        warnings.append(f"{d}: voice unknown: {exc}")
        return None


def _offline_card(d: str, facts, narrator, family) -> dict:
    c = {"domain": d, "family": family[0], "siblings": family[1]}
    if facts and facts.get("purpose"):
        c["purpose"] = facts["purpose"]
    c["voice"] = None if narrator is None else {"narrator": narrator["name"], "entity_class": narrator["entity_class"],
                                                "first_person": True}
    if facts:
        c["time"] = {"rule": facts.get("time_rule") or {}}
    return c


def corpus() -> dict:
    warnings: list = []
    names = _known_domains()
    tree = card.family_tree(names)
    try:
        cats = _catalogues(names)
    except Exception as exc:
        from artmind.cli_conventions import store_failure

        failure = store_failure(exc)
        if failure is None:
            raise  # a bug, not an unreachable store
        warnings.append(f"graph unreachable at {failure[1]}: cards carry schema facts only")
        cats = None
    cards = []
    for d in names:
        facts, narrator, family = _facts(d, warnings), _narrator(d, warnings), card.family_of(d, tree)
        if cats is None:
            cards.append(_offline_card(d, facts, narrator, family))
        else:
            cards.append(card.build_card(cats[d], facts, narrator, family,
                                         children=len(tree.get(d, [])), warnings=warnings))
    gens = [c.fingerprint[0] for c in (cats or {}).values() if c.fingerprint]
    built = max((c.built_at for c in (cats or {}).values()), default=None)
    rows, cuts = budget.fit_corpus(cards)
    return {"warnings": warnings, "next": NEXT_PEEL, "peel": 1,
            "corpus": card.corpus_block(cards, tree, generation=sum(gens), built_at=built),
            "domains": rows, "cuts": cuts}


def _expand(domains) -> list:
    """A family root opens its children (catalogue parts roll children up, so a root block would
    repeat them); a leaf opens itself."""
    known = _known_domains()
    out = []
    for d in domains:
        out += [k for k in known if k.startswith(d + ".")] or [d]
    return list(dict.fromkeys(out))


def domains(requested) -> dict:
    warnings: list = []
    names = _expand(requested)
    tree = card.family_tree(_known_domains())
    cats = _catalogues(names)
    blocks = []
    for d in names:
        facts, narrator = _facts(d, warnings), _narrator(d, warnings)
        c = card.build_card(cats[d], facts, narrator, card.family_of(d, tree), warnings=warnings)
        blocks.append(domain.build_block(cats[d], facts, card=c, warnings=warnings))
    blocks, cuts = budget.fit_domains(blocks)
    return {"warnings": warnings, "next": None, "peel": 2, "domains": blocks, "cuts": cuts}
