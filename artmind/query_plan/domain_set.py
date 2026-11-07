"""The domain decision as a set of domains (fix B1).

The classifier answers "which domain holds the answer?" with a probability per option. A
question whose answer spans several domains spreads that mass, and picking the top option
alone searches only part of the scope. This module turns the answer into the set the plan
searches, in code, by these rules (first that applies):

| `set_rule` | When | Selected |
|---|---|---|
| `given` | `--domain` was passed | those domains |
| `single` | the vault offers one option, or one option clears the margin | that option |
| `named` | the question names domains (name, name with spaces, a `domain_aliases:` word) | the named ones |
| `chunks_only_all` | every option is chunks-only (`graph_extraction: false`) | every option |
| `all` | the classifier chose "no single domain", or no option has mass | every domain |
| `family_parent` | `FAMILY_SPREAD_CHILDREN`+ children of one family each hold `FAMILY_SPREAD_SHARE` | the family parent (+ any other option in the margin) |
| `margin` | runners-up within `MARGIN` of the top option's share | the top option and those runners-up |

B2 (`rescope`, once the route is known) moves a set **the classifier decided** (`DomainSet.decided`:
`margin`, `family_parent`, and `single` in a vault with more than one option; never `given`, `named`,
`chunks_only_all`, `all`, or a one-option vault) to where the catalogue's evidence says the answer lives.
A family is the domains sharing a root, the part of the name before its first `.`:

| `set_rule` | Route | Selected |
|---|---|---|
| `family` | `search`, `survey`; or an entity/document route where nothing resolved | the family root |
| `resolved` | `entity_profile`, `entity_history`, `hierarchy`, `path` (entity); `document`, `time_history` (document) | the minimal cover of the domains the entity (or document) and its twins live in |
| `stores` | `structured` | the family's domains with structured tables |

Every other route and a domain outside any family keep B1's set.

`coverage` is the probability mass of the options the selected set covers (a parent covers its
children), with the classifier's "no single domain" mass in the denominator. It is the `domain`
stage of plan confidence: a plan that searches one domain while 40 % of the mass sits elsewhere
comes out below any threshold. A named, single-option or chunks-only-all set covers its scope: 1.0.

`stage` is the value the plan's `domain` stage takes: the coverage, except for a set that covers
every option of a vault of graph domains (`whole_vault`): searching everything is no domain
decision, like `all`, so widening to it earns nothing and the stage is the classifier's
confidence in its own top choice. A chunks-only vault is the exception the user decided: with no
domain named, all of its domains are the scope (1.0).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from artmind.query_plan import candidates

#: A runner-up joins the top domain when its share of the options' mass is at most this far
#: below the top option's share (shares sum to 1 over the domain options; the "no single
#: domain" option is left out of the shares). 0.15 keeps a clear winner alone (0.6 vs 0.25)
#: and takes a near tie (0.45 vs 0.35) as both.
MARGIN = 0.15
#: A child holding at least this share of the options' mass counts toward a spread family.
FAMILY_SPREAD_SHARE = 0.10
#: This many spread children of one family select the family parent.
FAMILY_SPREAD_CHILDREN = 3
#: A domain name form shorter than this never counts as naming the domain.
MIN_NAME_CHARS = 3


@dataclass(frozen=True)
class DomainSet:
    selected: list[str]
    source: str                 # given | matched | parent | set | all
    confidence: float | None    # the classifier's confidence in its own choice (None when given)
    coverage: float | None      # the mass the selected set covers; the `domain` stage (None when given)
    set_rule: str
    candidates: list[dict] = field(default_factory=list)
    named: list[str] = field(default_factory=list)
    whole_vault: bool = False   # a graph-domain set covering every option: no domain decision
    stage: float | None = None  # the `domain` stage; None when given
    decided: bool = False       # the classifier chose the set (B2 `rescope` may move it)
    family: str | None = None   # the family root `rescope` looked at (None when it did not apply)
    store_domains: tuple[str, ...] | None = None  # the table-holding domains of a `stores` set
    resolution: dict | None = None  # {entity|document: name, domains: [...]} of a `resolved` set
    tie_break: str | None = None    # "classifier" when the classifier's share picked among entities

    def __post_init__(self):
        if self.stage is None and self.coverage is not None:
            object.__setattr__(self, "stage", self.confidence if self.whole_vault else self.coverage)


def minimal_cover(scope) -> list[str]:
    """The scope without a domain whose parent is also in it, sorted."""
    unique = sorted(set(scope))
    return [d for d in unique if not any(d != e and candidates.covered_by(d, e) for e in unique)]


def _forms(key: str) -> set[str]:
    low = key.casefold()
    return {f for f in (low, re.sub(r"[._-]+", " ", low).strip()) if len(f) >= MIN_NAME_CHARS}


def named_domains(question: str, options, aliases: dict[str, tuple[str, ...]] | None = None) -> list[str]:
    """Options the question names as whole words: the key, the key with `.`, `_`, `-` read as
    spaces, or one of its `domain_aliases`. A match inside a longer match ("banking" inside
    "banking reference") does not count. Minimal cover, sorted."""
    low = question.casefold()
    hits: list[tuple[int, int, str]] = []
    for option in options:
        forms = _forms(option.key) | {a.strip().casefold() for a in (aliases or {}).get(option.key, ())
                                      if len(a.strip()) >= MIN_NAME_CHARS}
        for form in forms:
            pattern = r"(?<!\w)" + r"[\s_.-]+".join(map(re.escape, re.split(r"[\s_.-]+", form))) + r"(?!\w)"
            hits += [(m.start(), m.end(), option.key) for m in re.finditer(pattern, low)]
    kept = [h for h in hits
            if not any(o[0] <= h[0] and h[1] <= o[1] and (o[1] - o[0]) > (h[1] - h[0]) for o in hits)]
    return minimal_cover(k for _, _, k in kept)


def covered_mass(selected: list[str], options, probabilities: dict[str, float]) -> float:
    """The probability of every option a selected domain covers (itself or a sub-domain)."""
    return sum(max(0.0, float(probabilities.get(o.key, 0.0))) for o in options
               if any(candidates.covered_by(o.key, s) for s in selected))


def _source(selected: list[str], options) -> str:
    if len(selected) != 1:
        return "set"
    option = next((o for o in options if o.key == selected[0]), None)
    return "matched" if option is not None and not option.is_parent else "parent"


def spread_family(shares: dict[str, float]) -> str | None:
    """The family parent whose children spread the mass: `FAMILY_SPREAD_CHILDREN` or more children
    (dotted keys under the same parent) each holding `FAMILY_SPREAD_SHARE`. Of several, the deepest,
    then the one holding the most mass."""
    groups: dict[str, list[str]] = {}
    for key, share in shares.items():
        if "." in key and share >= FAMILY_SPREAD_SHARE:
            groups.setdefault(key.rsplit(".", 1)[0], []).append(key)
    spread = [p for p, kids in groups.items() if len(kids) >= FAMILY_SPREAD_CHILDREN]
    if not spread:
        return None
    return max(spread, key=lambda p: (p.count("."), sum(shares[k] for k in groups[p]), p))


def chunks_only(key: str, overview: list[dict]) -> bool:
    """Every overview row under `key` builds no graph (`graph_extraction: false`)."""
    rows = [r for r in overview if isinstance(r.get("domain"), str) and candidates.covered_by(r["domain"], key)]
    return bool(rows) and all(r.get("graph_extraction") is False for r in rows)


def decide(question: str, options, overview: list[dict], answer, all_key: str, *, given=None,
           aliases: dict[str, tuple[str, ...]] | None = None, shown: list[dict] | None = None) -> DomainSet:
    """The domain set for a question (rules in the module docstring). `answer` is the classifier's
    domain answer (None when it was not asked: given domains, or at most one option)."""
    shown = list(shown or [])
    if given:
        return DomainSet(list(given), "given", None, None, "given")
    if not options:
        return DomainSet([], "all", 0.0, 0.0, "all")
    if len(options) == 1:
        only = options[0]
        return DomainSet([only.key], "parent" if only.is_parent else "matched", 1.0, 1.0, "single")
    probabilities = dict(answer.probabilities) if answer is not None else {}
    confidence = answer.confidence if answer is not None else 0.0
    named = named_domains(question, options, aliases)
    if named:
        return DomainSet(named, _source(named, options), confidence, 1.0, "named", shown, named,
                         _whole_vault(named, options))
    if all(chunks_only(o.key, overview) for o in options):
        keys = minimal_cover(o.key for o in options)
        return DomainSet(keys, _source(keys, options), confidence, 1.0, "chunks_only_all", shown)
    mass = {o.key: max(0.0, float(probabilities.get(o.key, 0.0))) for o in options}
    total = sum(mass.values())
    if answer is None or answer.choice == all_key or total <= 0:
        every = [r["domain"] for r in overview if r.get("domain")]
        return DomainSet(every, "all", 0.0, 0.0, "all", shown)
    shares = {k: m / total for k, m in mass.items()}
    top = max(shares.values())
    picked = [k for k, s in shares.items() if s > 0 and s >= top - MARGIN - 1e-9]
    rule = "margin"
    family = spread_family(shares)
    if family is not None:
        picked = [k for k in picked if not candidates.covered_by(k, family)] + [family]
        rule = "family_parent"
    selected = minimal_cover(picked)
    if rule == "margin" and len(selected) == 1:
        rule = "single"
    coverage = round(covered_mass(selected, options, probabilities), 4)
    return DomainSet(selected, _source(selected, options), confidence, coverage, rule, shown,
                     whole_vault=len(selected) > 0 and _whole_vault(selected, options), decided=True)


def _whole_vault(selected: list[str], options) -> bool:
    """The set covers every option, and there was more than one to choose from."""
    return len(options) > 1 and all(any(candidates.covered_by(o.key, s) for s in selected) for o in options)


# ── B2: scope by evidence in families of domains ──────────────────────────────────────────

FAMILY_ROUTES = frozenset({"search", "survey"})
ENTITY_ROUTES = frozenset({"entity_profile", "entity_history", "hierarchy", "path"})
DOCUMENT_ROUTES = frozenset({"document", "time_history"})
STORE_ROUTES = frozenset({"structured"})


def family_root(domain: str) -> str:
    """The part of a domain name before its first `.` (`X` for `X.a` and `X.a.b`)."""
    return domain.split(".", 1)[0]


def families(all_domains) -> dict[str, list[str]]:
    """Root -> member domains, for roots with two or more members (a root that is itself a domain
    counts). A domain with no dot and no children is in no family."""
    groups: dict[str, set[str]] = {}
    for d in all_domains:
        groups.setdefault(family_root(d), set()).add(d)
    return {root: sorted(members) for root, members in groups.items() if len(members) > 1}


#: One thing a name resolved to: (name, the domains it lives in; several for twins).
Group = tuple[str, tuple[str, ...]]
#: (kind `entity`|`document`, family root) -> one list of groups per subject the route takes
#: (one; two for `path`). A subject with no group did not resolve.
Resolver = Callable[[str, str], "list[list[Group]]"]
#: family root -> the domains of the family that hold structured tables.
StoreLookup = Callable[[str], "list[str]"]


def _pick(groups: list[Group], probabilities: dict[str, float]) -> tuple[Group, bool]:
    """One group of a subject whose name resolved to different entities: the one living in the child
    with the highest classifier share. (group, True) when the share decided, else the first."""
    if len(groups) == 1:
        return groups[0], False
    share = [max((max(0.0, float(probabilities.get(d, 0.0))) for d in g[1]), default=0.0) for g in groups]
    best = max(share)
    if share.count(best) == 1 and best > 0:
        return groups[share.index(best)], True
    return groups[0], False


def rescope(dset: DomainSet, route: str | None, *, options, probabilities: dict[str, float], all_domains,
            resolve: Resolver | None = None, stores: StoreLookup | None = None) -> DomainSet:
    """Move a classifier-decided set to where the evidence says the answer lives (module docstring).
    Returns `dset` unchanged when it does not apply; `family` etc. record what it looked at."""
    if not dset.decided or not dset.selected or route is None:
        return dset
    kind = ("family" if route in FAMILY_ROUTES else "entity" if route in ENTITY_ROUTES
            else "document" if route in DOCUMENT_ROUTES else "stores" if route in STORE_ROUTES else None)
    fams = families(all_domains)
    roots = sorted({family_root(d) for d in dset.selected if family_root(d) in fams})
    if kind is None or not roots:
        return dset
    new = [d for d in dset.selected if family_root(d) not in roots]
    stage_set = list(new)
    hows: set[str] = set()
    subject: tuple[str, str] | None = None
    resolved_domains: list[str] = []
    tie = False
    store_domains: list[str] = []
    for root in roots:
        members = [d for d in dset.selected if family_root(d) == root]
        how, domains = "family", [root]
        if kind in ("entity", "document") and resolve is not None:
            subjects = [s for s in resolve(kind, root) if s]
            if subjects:
                picked = []
                for groups in subjects:
                    group, by_share = _pick(groups, probabilities)
                    tie = tie or by_share
                    picked.append(group)
                how, domains = "resolved", [d for g in picked for d in g[1]]
                subject = (kind, " / ".join(g[0] for g in picked))
                resolved_domains += domains
        elif kind == "stores":
            tables = list(stores(root)) if stores is not None else []
            if not tables:  # no table in the family: nothing to scope by, B1's members stay
                new += members
                stage_set += members
                continue
            how, domains = "stores", tables
            store_domains += tables
        new += domains
        stage_set.append(root)
        hows.add(how)
    meta = {"family": roots[0]}
    if not hows:
        return replace(dset, stage=None, **meta)
    selected = minimal_cover(new)
    shown = ({"resolution": {subject[0]: subject[1], "domains": minimal_cover(resolved_domains)},
              "tie_break": "classifier" if tie else None} if subject is not None and "resolved" in hows else {})
    if set(selected) == set(dset.selected):  # the evidence agrees with B1: the rule stays, the evidence is recorded
        return replace(dset, stage=None, **shown, **meta)
    rule = "resolved" if "resolved" in hows else "stores" if "stores" in hows else "family"
    stage_cover = minimal_cover(stage_set)
    return replace(
        dset, selected=selected, source=_source(selected, options), set_rule=rule,
        coverage=round(covered_mass(stage_cover, options, probabilities), 4),
        whole_vault=_whole_vault(stage_cover, options), stage=None,
        store_domains=tuple(minimal_cover(store_domains)) if store_domains else None,
        resolution=({subject[0]: subject[1], "domains": minimal_cover(resolved_domains)}
                    if subject is not None and "resolved" in hows else None),
        tie_break="classifier" if tie else None, **meta)


def with_guidance(dset: DomainSet, options) -> DomainSet:
    """A `stores` set whose store kind is `records_plus_guidance` also searches the family root, where
    the policy text of the records sits. `store_domains` keeps the table domains."""
    if dset.set_rule != "stores" or not dset.family:
        return dset
    selected = minimal_cover([*dset.selected, dset.family])
    return replace(dset, selected=selected, source=_source(selected, options), stage=None)
