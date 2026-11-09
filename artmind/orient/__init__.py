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


def _known_domains(warnings: list | None = None) -> list:
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
        from artmind.cli_conventions import store_failure

        if store_failure(exc) is None:
            raise  # a bug, not an unreachable store
        logger.debug("orient: no graph domains: {}", exc)
        if warnings is not None:
            warnings.append("graph unreachable: domains with only graph data are not listed")
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
    names = _known_domains(warnings)
    tree = card.family_tree(names)
    try:
        cats = _catalogues(names)
    except Exception as exc:
        from artmind.cli_conventions import store_failure

        failure = store_failure(exc)
        if failure is None:
            raise  # a bug, not an unreachable store
        warnings.append("graph unreachable: cards carry schema facts only")
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
    built = min((c.built_at for c in (cats or {}).values()), default=None)
    rows, cuts = budget.fit_corpus(cards)
    return {"warnings": warnings, "next": NEXT_PEEL, "peel": 1,
            "corpus": card.corpus_block(cards, tree, generation=sum(gens), built_at=built),
            "domains": rows, "cuts": cuts}


def _expand(domains, known) -> list:
    """A family root opens its children (catalogue parts roll children up, so a root block would
    repeat them); a leaf opens itself."""
    out = []
    for d in domains:
        out += [k for k in known if k.startswith(d + ".")] or [d]
    return list(dict.fromkeys(out))


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _root_data(requested, names, cats, warnings: list) -> None:
    """A root's catalogue rolls its children up, so more than they hold means data at the root itself."""
    for r in requested:
        kids = [k for k in names if k.startswith(r + ".")]
        if not kids or r not in cats or any(k not in cats for k in kids):
            continue
        docs = cats[r].fingerprint[2] - sum(cats[k].fingerprint[2] for k in kids)
        ents = cats[r].fingerprint[1] - sum(cats[k].fingerprint[1] for k in kids)
        if docs > 0 or ents > 0:
            warnings.append(
                f"{r}: {_plural(max(docs, 0), 'document', 'documents')} and "
                f"{_plural(max(ents, 0), 'entity', 'entities')} sit at the root itself and are not shown; "
                f"retrieval commands with --domain {r} include them")


def domains(requested) -> dict:
    warnings: list = []
    known = _known_domains(warnings)
    names = _expand(requested, known)
    tree = card.family_tree(known)
    roots = [r for r in requested if r not in names]
    cats = _catalogues(list(dict.fromkeys(names + roots)))
    _root_data(requested, names, cats, warnings)
    blocks = []
    for d in names:
        facts, narrator = _facts(d, warnings), _narrator(d, warnings)
        c = card.build_card(cats[d], facts, narrator, card.family_of(d, tree), warnings=warnings)
        blocks.append(domain.build_block(cats[d], facts, card=c, warnings=warnings))
    blocks, cuts = budget.fit_domains(blocks)
    return {"warnings": warnings, "next": None, "peel": 2, "domains": blocks, "cuts": cuts}
