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
from dataclasses import dataclass, field

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
                     whole_vault=len(selected) > 0 and _whole_vault(selected, options))


def _whole_vault(selected: list[str], options) -> bool:
    """The set covers every option, and there was more than one to choose from."""
    return len(options) > 1 and all(any(candidates.covered_by(o.key, s) for s in selected) for o in options)
