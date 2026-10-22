"""Deterministic candidates for `query plan` (spec 2026-10-22 §3.1, §4.2).

What the question mentions and what the graph and catalogue hold. No classifier
here: everything is code, so the same question and graph give the same candidates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import zip_longest

from loguru import logger

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
compare calculate identify name count check look get make remind
""".split())
TEMPORAL_WORDS = frozenset("""
january february march april may june july august september october november december
jan feb mar apr jun jul aug sep sept oct nov dec
monday tuesday wednesday thursday friday saturday sunday
mon tue tues wed thu thur thurs fri sat sun
""".split())
JOINERS = frozenset("of de van von der da du la le".split())
HONORIFICS = frozenset("dr mr mrs ms prof".split())
ME_BLOCKERS = frozenset("tell show give list send remind let help".split())
# Words that carry no search value in a keyword list, beyond STOPWORDS and QUESTION_WORDS.
_KEYWORD_STOP = frozenset("many much get make say do does to a".split())
_CONTRACTIONS_WITH_I = frozenset({"i'm", "i've", "i'd", "i'll"})
_AGGREGATE_RE = re.compile(r"\b(per|total|average|how many|number of|count of|sum of)\b")
_GUIDANCE_RE = re.compile(r"\b(policy|policies|guidance|should|what to do|rules?|procedures?)\b")

_TOKEN_RE = re.compile(r"[^\W_][\w'’&-]*")
_QUOTED_RE = re.compile(r'"([^"]{2,})"|“([^”]{2,})”')
_KEYWORD_RE = re.compile(r"\w[\w'’-]*")
_NAME_PART_RE = re.compile(r"[^\W_]+")
_SENTENCE_BREAK_RE = re.compile(r"(?:[.?!:]|\n)\s*$")


@dataclass(frozen=True)
class Mention:
    text: str
    start: int
    end: int
    via: str  # capitalised | quoted | first_person


@dataclass(frozen=True)
class _Token:
    start: int
    end: int  # after trailing punctuation and a possessive suffix are stripped
    word: str
    low: str
    possessive: bool
    in_quote: bool


def _tokens(question: str, quoted: list[tuple[int, int]]) -> list[_Token]:
    out: list[_Token] = []
    for match in _TOKEN_RE.finditer(question):
        word = match.group(0).rstrip("'’-&")
        possessive = False
        if len(word) > 2 and word[-2] in "'’" and word[-1] in "sS" and word.casefold().replace("’", "'") \
                not in _CONTRACTIONS_WITH_I:
            word, possessive = word[:-2], True
        if not word:
            continue
        start = match.start()
        out.append(_Token(start, start + len(word), word, word.casefold(), possessive,
                          any(a <= start < b for a, b in quoted)))
    return out


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
    shouting = question.isupper()  # an all-caps question carries no capitalisation signal
    tokens = _tokens(question, taken)
    seen_first_person = False
    run: list[_Token] = []

    def flush() -> None:
        if run:
            mentions.append(Mention(question[run[0].start:run[-1].end], run[0].start, run[-1].end, "capitalised"))
            run.clear()

    def gap_between(a: _Token, b: _Token) -> str:
        return question[a.end:b.start]

    def capitalised(token: _Token | None) -> bool:
        return (token is not None and not token.in_quote and not shouting
                and token.word[:1].isupper() and token.low not in TEMPORAL_WORDS)

    def joins(prev: _Token, token: _Token) -> bool:
        """Can `token` continue a run that ends at `prev`?"""
        gap = gap_between(prev, token)
        if not gap.strip() and "\n" not in gap:
            return True
        return prev.low in HONORIFICS and gap.strip() == "."

    prev_low = ""
    for index, token in enumerate(tokens):
        previous, prev_low = prev_low, token.low
        if token.in_quote:
            flush()
            continue
        pronoun = token.low.replace("’", "'")
        if pronoun in _CONTRACTIONS_WITH_I and "i" in first:
            flush()
            if not seen_first_person:
                mentions.append(Mention(token.word[:1], token.start, token.start + 1, "first_person"))
                seen_first_person = True
            continue
        if token.low in first and not (token.low == "me" and previous in ME_BLOCKERS):
            flush()
            if not seen_first_person:
                mentions.append(Mention(token.word, token.start, token.end, "first_person"))
                seen_first_person = True
            continue
        following = tokens[index + 1] if index + 1 < len(tokens) else None
        sentence_start = not re.sub(r"[\W_]+", "", question[:token.start]) \
            or _SENTENCE_BREAK_RE.search(question[:token.start]) is not None
        is_name = (
            not shouting
            and token.word[:1].isupper()
            and not (sentence_start and (token.low in QUESTION_WORDS or token.low in STOPWORDS))
            and token.low not in TEMPORAL_WORDS
        )
        if not is_name and token.low in HONORIFICS and capitalised(following) \
                and question[token.end:following.start].strip() in ("", "."):
            is_name = True  # "dr smith"
        if is_name:
            if run and not joins(run[-1], token):
                flush()
            run.append(token)
            if token.possessive:
                flush()
        elif (run and token.low in JOINERS and capitalised(following) and not token.possessive
              and joins(run[-1], token) and joins(token, following)):
            run.append(token)  # "Bank of England"
        else:
            flush()
    flush()
    if len(mentions) > MAX_MENTIONS:
        def priority(m: Mention) -> int:
            if m.via == "quoted":
                return 0
            if m.via == "capitalised":
                return 1 if len(m.text.split()) > 1 else 2
            return 3

        mentions = sorted(sorted(mentions, key=lambda m: (priority(m), m.start))[:MAX_MENTIONS],
                          key=lambda m: m.start)
    mentions.sort(key=lambda m: m.start)
    return mentions


def keywords(question: str, mentions: list[Mention]) -> list[str]:
    """Content words outside the mentions, in question order (the `{keywords}` placeholder)."""
    spans = [(m.start, m.end) for m in mentions]
    out: list[str] = []
    for match in _KEYWORD_RE.finditer(question):
        if any(a <= match.start() < b for a, b in spans):
            continue
        word = re.sub(r"['’]s$", "", match.group(0).casefold().strip("'’-"))
        if (len(word) >= 2 and not word.isdigit() and word not in out
                and word not in STOPWORDS and word not in QUESTION_WORDS
                and word not in TEMPORAL_WORDS and word not in _KEYWORD_STOP):
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


def _interleave(lists: list[list[str]]) -> list[str]:
    """Round-robin across the lists, first occurrence wins."""
    out: list[str] = []
    for tier in zip_longest(*lists):
        out += [x for x in tier if x is not None and x not in out]
    return out


def _option(key: str, rows: list[dict], is_parent: bool) -> DomainOption:
    documents = _interleave([list(r.get("documents") or []) for r in rows])
    classes = _interleave([list(r.get("top_classes") or []) for r in rows])
    tables: list[str] = []
    for row in rows:
        tables += [t for t in row.get("tables") or [] if t not in tables]
    return DomainOption(key, is_parent, sum(r.get("document_count") or 0 for r in rows),
                        tuple(documents[:10]), tuple(classes[:8]), tuple(tables))


def domain_options(rows: list[dict]) -> list[DomainOption]:
    """Every leaf domain from `domains-overview`, plus each family parent with two
    or more children (a parent scopes all of them; study P14). Of nested parents
    that scope the same set, only the deepest is offered."""
    by_name = {r["domain"]: r for r in rows if isinstance(r.get("domain"), str)}
    children: dict[str, set[str]] = {}
    for name in by_name:
        parts = name.split(".")
        for i in range(1, len(parts)):
            children.setdefault(".".join(parts[:i]), set()).add(name)
    scope = {parent: frozenset(kids | ({parent} if parent in by_name else set()))
             for parent, kids in children.items() if len(kids) >= 2}
    deepest = {}
    for parent, members in scope.items():
        if members not in deepest or parent.count(".") > deepest[members].count("."):
            deepest[members] = parent
    options = []
    for parent, members in scope.items():
        if deepest[members] != parent:
            continue
        kids = children[parent]
        rows_for = [by_name[k] for k in sorted(kids)] + ([by_name[parent]] if parent in by_name else [])
        options.append(_option(parent, rows_for, True))
    covered = {parent for parent in scope if parent in by_name}  # a domain folded into its parent
    options += [_option(name, [row], False) for name, row in by_name.items()
                if name not in {o.key for o in options} and name not in covered]
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


def _name_tokens(text: str) -> set[str]:
    return set(_NAME_PART_RE.findall(text.casefold()))


def _names_overlap(mention: str, name: str) -> bool:
    """Every token of one side appears in the other (fulltext's evidence bar)."""
    a, b = _name_tokens(mention), _name_tokens(name)
    return bool(a) and bool(b) and (a <= b or b <= a)


def resolve_mention(mention: Mention, domains: list[str], narrator) -> list[dict]:
    """Candidates for one mention. Only name evidence counts, and no embedding is
    computed: an `exact` hit is kept; a `fulltext` hit only when one side's tokens all
    appear in the other, since Lucene returns near-misses (the kg_views rule)."""
    if mention.via == "first_person":
        return narrator_candidates(narrator, domains) if narrator else []
    result = vector_query.entity_name_candidates(domains, mention.text, RESOLVE_TOP_K)
    out = []
    for row in result.get("rows", []):
        entity, legs = row.get("entity"), set(row.get("matched_by") or [])
        if not entity:
            continue
        if "exact" in legs or ("fulltext" in legs and _names_overlap(mention.text, str(entity.get("name") or ""))):
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
        MATCH (d:Document|DocumentHistory)
        WHERE {domain_predicate("d")}
        RETURN DISTINCT d.name AS name
        """,
        {"domains": domains},
    )
    return sorted({r["name"] for r in rows if r.get("name")})


def _norm(text: str) -> str:
    text = re.sub(r"(?:[^\w.]|_)+", " ", text.casefold())
    return " ".join(re.sub(r"\.(?![^\W_])", " ", text).split())


def match_document(question: str, names: list[str]) -> str | None:
    """The longest document name (with or without its suffix) the question spells out.

    A bare stem is weak evidence: it counts only with two or more tokens, a digit or
    underscore in the name, or eight characters. Spelling the suffix always counts."""
    q = f" {_norm(question)} "
    best: tuple[str, int] | None = None
    for name in sorted(names):
        stem = name
        for suffix in DOCUMENT_SUFFIXES:
            if stem.casefold().endswith(suffix):
                stem = stem[: -len(suffix)]
                break
        strong_name = bool(re.search(r"[\d_]", name))
        forms = {_norm(stem): False}
        if stem != name:
            forms[_norm(name)] = True
        for form, spells_suffix in forms.items():
            if len(form) < 4 or f" {form} " not in q:
                continue
            if not (spells_suffix or len(form.split()) >= 2 or strong_name or len(form) >= 8):
                continue
            if best is None or len(form) > best[1]:
                best = (name, len(form))
    return best[0] if best else None


def store_facts(domains: list[str], question: str, resolved_classes: set) -> dict | None:
    """Which of structured-store.md's four cases applies (S7-S9), or None without tables."""
    try:
        from artmind.structured import registry

        surface = registry.routing_surface(domains)
    except Exception as exc:  # a query-only host has no registry DB
        logger.debug("store facts unavailable: {}", exc)
        return None
    if not surface:
        return None
    low = question.casefold()
    bridged = sorted({c["entity_class"] for t in surface for c in t.get("entity_classes") or []})
    hit = set(bridged) & {c for c in resolved_classes if c}
    aggregate = _AGGREGATE_RE.search(low) is not None
    if hit and (aggregate or re.search(r"\d", low)):
        kind = "hybrid"
    elif aggregate:
        kind = "analytical"
    elif hit and _GUIDANCE_RE.search(low):
        kind = "records_plus_guidance"
    else:
        kind = "graph"
    # matched_values is not populated in v1: routing_surface() carries no cell values.
    return {"kind": kind, "tables": sorted({t["table"] for t in surface}), "bridge_classes": bridged,
            "matched_values": []}
