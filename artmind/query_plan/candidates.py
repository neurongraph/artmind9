"""Deterministic candidates for `query plan` (spec 2026-10-22 §3.1, §4.2).

What the question mentions and what the graph and catalogue hold. No classifier
here: everything is code, so the same question and graph give the same candidates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from artmind import graph_query, vector_query
from artmind.graph_query import domain_predicate
from artmind.query_plan.classify.lexical import STOPWORDS

MAX_MENTIONS = 8
MAX_CANDIDATES = 5
RESOLVE_TOP_K = 10
DOCUMENT_SUFFIXES = (".md", ".pdf", ".docx", ".txt", ".html")
QUESTION_WORDS = frozenset("""
how what when where which who whom whose why did does do is are was were can could should would will
has have had list show tell give find summarise summarize describe explain any all in on for from of the
a an between since during please
""".split())
MONTHS = frozenset("""
january february march april may june july august september october november december
jan feb mar apr jun jul aug sep sept oct nov dec
""".split())
AGGREGATE_WORDS = ("how many", "average", "total", "sum of", "count of", "per ", "number of")
GUIDANCE_WORDS = ("policy", "guidance", "should", "what to do", "rule", "procedure")

_TOKEN_RE = re.compile(r"[A-Za-z0-9][\w'’&-]*")
_QUOTED_RE = re.compile(r'"([^"]{2,})"|“([^”]{2,})”')
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’-]*")


@dataclass(frozen=True)
class Mention:
    text: str
    start: int
    end: int
    via: str  # capitalised | quoted | first_person


def find_mentions(question: str, first_person: tuple[str, ...] = ("i", "me", "my", "mine", "myself")) -> list[Mention]:
    """Quoted spans, runs of capitalised words, and one first-person word."""
    mentions: list[Mention] = []
    taken: list[tuple[int, int]] = []
    for match in _QUOTED_RE.finditer(question):
        text = (match.group(1) or match.group(2)).strip()
        start = match.start() + 1
        mentions.append(Mention(text, start, start + len(text), "quoted"))
        taken.append((match.start(), match.end()))
    first = {w.casefold() for w in first_person}
    seen_first_person = False
    run: list[re.Match] = []

    def flush() -> None:
        if run:
            text = re.sub(r"['’]s$", "", question[run[0].start():run[-1].end()])
            mentions.append(Mention(text, run[0].start(), run[0].start() + len(text), "capitalised"))
            run.clear()

    for index, match in enumerate(_TOKEN_RE.finditer(question)):
        if any(a <= match.start() < b for a, b in taken):
            flush()
            continue
        word = match.group(0)
        low = re.sub(r"['’]s$", "", word).casefold()
        if low in first:
            flush()
            if not seen_first_person:
                mentions.append(Mention(word, match.start(), match.end(), "first_person"))
                seen_first_person = True
            continue
        sentence_start = index == 0 or re.search(r"[.?!]\s*$", question[:match.start()]) is not None
        is_name = (
            word[:1].isupper()
            and not (sentence_start and (low in QUESTION_WORDS or low in STOPWORDS))
            and low not in MONTHS
        )
        if is_name:
            if run and question[run[-1].end():match.start()].strip():
                flush()
            run.append(match)
        else:
            flush()
    flush()
    mentions.sort(key=lambda m: m.start)
    return mentions[:MAX_MENTIONS]


def keywords(question: str, mentions: list[Mention]) -> list[str]:
    """Content words outside the mentions, in question order (the `{keywords}` placeholder)."""
    spans = [(m.start, m.end) for m in mentions]
    out: list[str] = []
    for match in _WORD_RE.finditer(question):
        if any(a <= match.start() < b for a, b in spans):
            continue
        word = match.group(0).casefold().strip("'’-")
        if len(word) >= 3 and word not in STOPWORDS and word not in QUESTION_WORDS and word not in out:
            out.append(word)
    return out


@dataclass(frozen=True)
class DomainOption:
    key: str
    is_parent: bool
    document_count: int
    documents: tuple[str, ...]
    top_classes: tuple[str, ...]
    tables: tuple[str, ...]

    def text(self) -> str:
        """The option text a decision model reads (spec §4.4 call 1)."""
        head = f"All {self.key}.* domains" if self.is_parent else self.key
        parts = [f"{head}: {self.document_count} documents"]
        if self.documents:
            parts.append("e.g. " + ", ".join(self.documents[:5]))
        if self.top_classes:
            parts.append("classes " + ", ".join(self.top_classes))
        if self.tables:
            parts.append("tables " + ", ".join(self.tables))
        return "; ".join(parts)


def _option(key: str, rows: list[dict], is_parent: bool) -> DomainOption:
    documents: list[str] = []
    classes: list[str] = []
    tables: list[str] = []
    for row in rows:
        documents += [d for d in row.get("documents") or [] if d not in documents]
        classes += [c for c in row.get("top_classes") or [] if c not in classes]
        tables += [t for t in row.get("tables") or [] if t not in tables]
    return DomainOption(key, is_parent, sum(r.get("document_count") or 0 for r in rows),
                        tuple(documents[:10]), tuple(classes[:8]), tuple(tables))


def domain_options(rows: list[dict]) -> list[DomainOption]:
    """Every leaf domain from `domains-overview`, plus each family parent with two
    or more children (a parent scopes all of them; study P14)."""
    by_name = {r["domain"]: r for r in rows if isinstance(r.get("domain"), str)}
    children: dict[str, set[str]] = {}
    for name in by_name:
        parts = name.split(".")
        for i in range(1, len(parts)):
            children.setdefault(".".join(parts[:i]), set()).add(name)
    options = []
    for parent, kids in children.items():
        if len(kids) >= 2:
            members = [by_name[k] for k in sorted(kids)] + ([by_name[parent]] if parent in by_name else [])
            options.append(_option(parent, members, True))
    parents = {o.key for o in options}
    options += [_option(name, [row], False) for name, row in by_name.items() if name not in parents]
    return sorted(options, key=lambda o: o.key)


def entity_ref(entity: dict, matched_by) -> dict:
    return {
        "_id": entity.get("_id"),
        "name": entity.get("name"),
        "entity_class": entity.get("entity_class"),
        "domain": entity.get("_domain"),
        "observation_count": entity.get("observation_count"),
        "connection_count": entity.get("connection_count"),
        "matched_by": sorted(matched_by or []),
        "description": (entity.get("description") or "")[:120],
    }


def narrator_candidates(narrator, domains: list[str]) -> list[dict]:
    rows = graph_query._run_read_query(
        f"""
        MATCH (e:Entity)
        WHERE {domain_predicate("e")} AND e.entity_class = $cls AND toLower(e.name) = toLower($name)
        RETURN e {{ ._id, .name, .entity_class, .description, ._domain,
                    observation_count: e._observation_count,
                    connection_count: COUNT {{ (e)-[:RELATES_TO]-(:Entity) }} }} AS entity
        ORDER BY e._observation_count DESC
        LIMIT $limit
        """,
        {"domains": domains, "cls": narrator.entity_class, "name": narrator.name, "limit": MAX_CANDIDATES},
    )
    return [entity_ref(r["entity"], {"narrator"}) for r in rows if r.get("entity")]


def resolve_mention(mention: Mention, domains: list[str], narrator) -> list[dict]:
    """Candidates for one mention. Only exact or fulltext evidence counts: the
    vector leg always returns neighbours, so it is not evidence (the kg_views rule)."""
    if mention.via == "first_person":
        return narrator_candidates(narrator, domains) if narrator else []
    result = vector_query.entity_resolve(domains, mention.text, RESOLVE_TOP_K)
    out = []
    for row in result.get("rows", []):
        entity, legs = row.get("entity"), set(row.get("matched_by") or [])
        if entity and legs & {"exact", "fulltext"}:
            out.append(entity_ref(entity, legs))
    return out[:MAX_CANDIDATES]


def twin_groups(refs: list[dict]) -> list[list[dict]]:
    """Candidates sharing a class and a casefolded name, in rank order (study P19)."""
    groups: dict[tuple[str, str], list[dict]] = {}
    for ref in refs:
        key = (str(ref.get("entity_class") or "").casefold(), str(ref.get("name") or "").casefold())
        groups.setdefault(key, []).append(ref)
    return list(groups.values())


def document_names(domains: list[str]) -> list[str]:
    rows = graph_query._run_read_query(
        f"""
        MATCH (d)
        WHERE (d:Document OR d:DocumentHistory) AND {domain_predicate("d")}
        RETURN DISTINCT d.name AS name
        """,
        {"domains": domains},
    )
    return sorted({r["name"] for r in rows if r.get("name")})


def _norm(text: str) -> str:
    text = re.sub(r"[^0-9a-z.]+", " ", text.casefold())
    return " ".join(re.sub(r"\.(?![0-9a-z])", " ", text).split())


def match_document(question: str, names: list[str]) -> str | None:
    """The longest document name (with or without its suffix) the question spells out."""
    q = f" {_norm(question)} "
    best: tuple[str, int] | None = None
    for name in names:
        stem = name
        for suffix in DOCUMENT_SUFFIXES:
            if stem.casefold().endswith(suffix):
                stem = stem[: -len(suffix)]
                break
        for form in {_norm(name), _norm(stem)}:
            if len(form) >= 4 and f" {form} " in q and (best is None or len(form) > best[1]):
                best = (name, len(form))
    return best[0] if best else None


def store_facts(domains: list[str], question: str, resolved_classes: set) -> dict | None:
    """Which of structured-store.md's four cases applies (S7-S9), or None without tables."""
    try:
        from artmind.structured import registry

        surface = registry.routing_surface(domains)
    except Exception:  # a query-only host has no registry DB
        return None
    if not surface:
        return None
    low = question.casefold()
    bridged = sorted({c["entity_class"] for t in surface for c in t.get("entity_classes") or []})
    hit = set(bridged) & {c for c in resolved_classes if c}
    aggregate = any(w in low for w in AGGREGATE_WORDS)
    if hit and (aggregate or re.search(r"\d", low)):
        kind = "hybrid"
    elif aggregate:
        kind = "analytical"
    elif hit and any(w in low for w in GUIDANCE_WORDS):
        kind = "records_plus_guidance"
    else:
        kind = "graph"
    return {"kind": kind, "tables": sorted({t["table"] for t in surface}), "bridge_classes": bridged,
            "matched_values": []}
