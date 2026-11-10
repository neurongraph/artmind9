"""A domain's mart declaration: `<DOMAIN_MART_DIR>/<domain>.yaml` (spec §5)."""
from __future__ import annotations

from dataclasses import dataclass, field

import yaml

import paths


class MartDeclarationError(ValueError):
    pass


@dataclass
class PersonDim:
    entity_class: str = "PERSON"
    seed_property: str | None = None
    words: str | None = None


@dataclass
class PlaceDim:
    entity_class: str = "LOCATION"
    seed_property: str | None = None
    seed_edges: list[str] = field(default_factory=list)


@dataclass
class TypeDim:
    classes: list[str] = field(default_factory=list)
    property: str = "type"
    families: dict[str, list[str]] = field(default_factory=dict)

    def family_of(self, value) -> str | None:
        v = str(value or "").strip().lower()
        for family, members in self.families.items():
            if v in {m.lower() for m in members}:
                return family
        return None


@dataclass
class Whole:
    kind: str
    source: str = "happening"
    families: list[str] = field(default_factory=list)
    place: str | None = None                 # "away" | None
    consecutive_days: int = 1
    share: list[str] = field(default_factory=list)
    alone_never_joins: list[str] = field(default_factory=list)
    anchor: str | None = None                # "person" | None
    match: list[str] = field(default_factory=list)
    window_gap_days: int | None = None


@dataclass
class MartDeclaration:
    domain: str
    version: int
    home_city: str | None
    happening_classes: list[str]
    mention_classes: list[str]
    calendar: bool
    person: PersonDim
    place: PlaceDim
    type: TypeDim
    wholes: dict[str, Whole]
    raw: dict


def declaration_path(domain: str):
    return paths.DOMAIN_MART_DIR / f"{domain}.yaml"


def load_declaration(domain: str) -> MartDeclaration | None:
    path = declaration_path(domain)
    if not path.is_file():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise MartDeclarationError(f"{path.name}: not valid YAML ({e})") from e
    if not isinstance(data, dict) or data.get("format") != 1:
        raise MartDeclarationError(f"{path.name}: needs `format: 1`")
    dims = data.get("dimensions") or {}
    person = dims.get("person") or {}
    place = dims.get("place") or {}
    typ = dims.get("type") or {}
    wholes = {}
    for kind, w in (data.get("wholes") or {}).items():
        where = w.get("where") or {}
        join = w.get("join") or {}
        wholes[kind] = Whole(
            kind=kind, source=w.get("from", "happening"), families=list(where.get("type_family") or []),
            place=where.get("place"), consecutive_days=int(join.get("consecutive_days", 1)),
            share=list(join.get("share") or []), alone_never_joins=list(w.get("alone_never_joins") or []),
            anchor=w.get("anchor"), match=list(w.get("match") or []),
            window_gap_days=(w.get("window") or {}).get("grows_while_gap_days"))
    facts = data.get("facts") or {}
    return MartDeclaration(
        domain=data.get("domain") or domain, version=int(data.get("version") or 1),
        home_city=(data.get("home") or {}).get("city"),
        happening_classes=list(facts.get("happening") or []), mention_classes=list(facts.get("mention") or []),
        calendar=bool((dims.get("time") or {}).get("calendar")),
        person=PersonDim(person.get("class", "PERSON"), (person.get("seed") or {}).get("property"), person.get("words")),
        place=PlaceDim(place.get("class", "LOCATION"), (place.get("seed") or {}).get("property"),
                       list((place.get("seed") or {}).get("edges") or [])),
        type=TypeDim(list(typ.get("classes") or []), typ.get("property", "type"),
                     {k: list(v or []) for k, v in (typ.get("families") or {}).items()}),
        wholes=wholes, raw=data)


def validate_declaration(decl: MartDeclaration, schema: dict) -> tuple[list[str], list[str]]:
    """Errors (refuse to build) and warnings against the domain's schema."""
    errors: list[str] = []
    warnings: list[str] = []
    classes = set((schema or {}).get("entity_types") or {})
    for label, names in (("facts.happening", decl.happening_classes), ("facts.mention", decl.mention_classes),
                         ("dimensions.type.classes", decl.type.classes)):
        for c in names:
            if c not in classes:
                errors.append(f"{label}: class {c!r} is not in the domain schema")
    for label, c in (("dimensions.person.class", decl.person.entity_class),
                     ("dimensions.place.class", decl.place.entity_class)):
        if c not in classes:
            errors.append(f"{label}: class {c!r} is not in the domain schema")
    for kind, w in decl.wholes.items():
        for fam in w.families:
            if fam not in decl.type.families:
                errors.append(f"wholes.{kind}: type_family {fam!r} is not declared under dimensions.type.families")
        if w.source != "happening":
            errors.append(f"wholes.{kind}: `from` must be `happening`")
        if w.anchor not in (None, "person"):
            errors.append(f"wholes.{kind}: anchor must be `person`")
    if not decl.home_city:
        warnings.append("home.city is not set: every place counts as away")
    return errors, warnings
