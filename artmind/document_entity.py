"""The document's own entity, named once in code (no LLM).

Chunks extract in isolation, so a document that is itself an instance of one
of its domain's classes -- a contract change note is an AMENDMENT -- is named
differently by almost every chunk: end to end, one amendment got eight names
over fourteen chunks (shaped like "CN012 Harbour Programme Sixth Amendment",
"CN12 Harbour Programme Amendment 2026-01-01", the bare filename, ...), and
neither the per-document LLM canonicalization nor a name suggested to every
chunk folded them.

This step folds them deterministically, after the LLM canonicalization:

1. A schema opts in, naming the classes a document of its domain can itself be
   an instance of::

       document_entity:
         classes: [AMENDMENT, CONTRACT]

   A schema without the key is untouched.
2. The document's references come from its filename and title, never from
   the model: letters followed by digits (`CN12`, `SR4410`, `P01234`),
   normalised so `CN12`, `CN012` and `CN 12` are one reference (`CN12`).
3. An extracted name of an opted-in class names the document when it carries
   one of those references and every other word in it is one of the
   document's own words (its title's), a document-type word (agreement,
   amendment, change note, ...), an ordinal or a date. A name with any other
   word -- "CN012 Appendix 2 Deliverables Change" -- names a part of
   the document and is left alone.
4. Per class, every such name is folded into the variant the most chunks
   used (ties: the earliest chunk, then the shorter name).
5. A deictic name of the same class -- "This Amendment", "This Transaction
   Contract": "this" and document-type words only, which a schema may tell the
   model to use where a passage omits the reference -- is folded in too, but
   into a reference-bearing name when the class has one. Left alone it carries
   no document at all, and since entities key on name, class and domain, every
   document's "This Amendment" would be one entity -- so where there is nothing
   to fold it into (a framework agreement's "This Transaction Contract", a
   document whose filename carries no reference) it is scoped to its document
   instead: "This Transaction Contract (<document title>)".

Schema-agnostic: nothing here names a domain or a class.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

#: A reference candidate: a word of 1-5 letters, an optional separator, then
#: 1-6 digits (`CN12`, `CN 48`, `SR4410`, `P01234`). See `_is_reference`.
_REFERENCE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]{1,5})([\s_-]?)(\d{1,6})(?![0-9])(?![./-]\d)")
#: A date as documents write them: 2026-01-01, 17.06.2024, 1/4/25, or a bare year.
_DATE = re.compile(r"^(\d{1,4}[-./]\d{1,2}[-./]\d{1,4}|(19|20)\d{2})$")
_WORD = re.compile(r"[A-Za-z]+|\d[\d./-]*")

#: Words a document's own name may carry besides its title's: what kind of
#: document it is, and filler. Lower case.
DOCUMENT_TYPE_WORDS = frozenset({
    "agreement", "amendment", "amended", "amending", "change", "note", "contract", "contracts",
    "variation", "order", "notice", "letter", "side", "statement", "work", "sow", "transaction",
    "addendum", "deed", "document", "terms", "schedule", "version", "v", "final", "issued", "draft",
    "ccn", "and", "of", "the", "to", "for", "a", "an", "dated", "no", "number", "ref", "reference",
})
_ORDINALS = frozenset({
    "first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth",
    "eleventh", "twelfth", "1st", "2nd", "3rd", "4th", "5th", "6th", "7th", "8th", "9th", "10th",
})


def _normalise_reference(letters: str, digits: str) -> str:
    return f"{letters.upper()}{int(digits)}"


def _is_reference(letters: str, separator: str, digits: str) -> bool:
    """Not every letters-then-digits is a reference: one letter needs two digits
    (`P01234` yes, `v1` no), and a word set apart by a space from a year is a
    word and a date ("Note 2026"), not a reference. A digit group followed by
    date punctuation is excluded by the pattern itself."""
    if len(letters) == 1 and len(digits) < 2:
        return False
    if separator.isspace() and len(digits) == 4 and digits[:2] in ("19", "20"):
        return False
    return True


def _reference_matches(text: str):
    return [m for m in _REFERENCE.finditer(text or "") if _is_reference(*m.groups())]


def references(*texts: str) -> set[str]:
    """The normalised references in `texts` (`CN12` and `CN012` -> `CN12`)."""
    return {
        _normalise_reference(m.group(1), m.group(3)) for text in texts for m in _reference_matches(text)
    }


def document_classes(schema: dict) -> list[str]:
    """The classes `document_entity.classes` opts in, those the schema declares."""
    setting = schema.get("document_entity")
    if not isinstance(setting, dict):
        return []
    declared = schema.get("entity_types") or {}
    classes = setting.get("classes") or []
    return [c for c in classes if isinstance(c, str) and c in declared]


def _own_words(title: str, filename: str) -> set[str]:
    words = {w.lower() for w in _WORD.findall(f"{title} {filename}") if w.isalpha()}
    return words | DOCUMENT_TYPE_WORDS | _ORDINALS


def names_document(name: str, refs: set[str], own_words: set[str]) -> bool:
    """Does `name` name the document itself? It carries one of `refs`, and every
    other word is one of `own_words` or a date."""
    if not refs or not (references(name) & refs):
        return False
    rest = name
    for match in reversed(_reference_matches(name)):
        rest = rest[: match.start()] + " " + rest[match.end():]
    for word in _WORD.findall(rest):
        if _DATE.match(word):
            continue
        if word.lower() not in own_words:
            return False
    return True


def is_deictic(name: str) -> bool:
    """`This Amendment`, `this transaction contract`: "this" followed only by
    document-type words. Names the document it sits in, whichever that is."""
    words = [w.lower() for w in _WORD.findall(name)]
    return len(words) >= 2 and words[0] == "this" and all(w in DOCUMENT_TYPE_WORDS for w in words[1:])


@dataclass(frozen=True)
class Fold:
    entity_class: str
    name: str
    folded: tuple[str, ...]
    #: True when `name` is a deictic name scoped to its document, not a fold
    #: into the document's own reference-bearing name.
    scoped: bool = False


def fold(
    entities: list[dict], canonical_names: dict[str, str], schema: dict, *, title: str, filename: str,
) -> tuple[dict[str, str], list[Fold]]:
    """`canonical_names` with every name of the document's own entity folded
    into one, per opted-in class, any deictic name left over scoped to the
    document, and what was done. Pure: returns a new mapping; the input is not
    changed. A schema without the opt-in returns the mapping as it was."""
    classes = document_classes(schema)
    if not classes:
        return canonical_names, []
    refs = references(title, filename)
    own = _own_words(title, filename)
    mapping = dict(canonical_names)
    folds: list[Fold] = []
    for entity_class in classes:
        # canonical name -> the chunks it came from, for this class's matching names
        chunks_by_canonical: dict[str, set[str]] = {}
        raw_names: set[str] = set()
        deictic: set[str] = set()
        for entity in entities:
            if entity.get("entity_class") != entity_class:
                continue
            raw = entity.get("name") or ""
            canonical = mapping.get(raw) or raw
            if not raw:
                continue
            if refs and (names_document(raw, refs, own) or names_document(canonical, refs, own)):
                raw_names.add(raw)
                chunks_by_canonical.setdefault(canonical, set()).add(entity.get("chunk_id") or "")
            elif is_deictic(raw) or is_deictic(canonical):
                deictic.add(raw)
        if chunks_by_canonical and len(chunks_by_canonical) + len(deictic) >= 2:
            chosen = min(
                chunks_by_canonical,
                key=lambda n: (-len(chunks_by_canonical[n]), min(chunks_by_canonical[n]), len(n), n),
            )
            for raw in raw_names | deictic:
                mapping[raw] = chosen
            folded = {n for n in chunks_by_canonical if n != chosen} | deictic
            folds.append(Fold(entity_class, chosen, tuple(sorted(folded))))
        elif deictic and not chunks_by_canonical:
            # Nothing to fold into: one name per document, never one per vault.
            first = min(deictic, key=lambda n: (not n[:1].isupper(), " ".join(n.split()).casefold(), n))
            scoped = f"{' '.join(first.split())} ({title})"
            for raw in deictic:
                mapping[raw] = scoped
            folds.append(Fold(entity_class, scoped, tuple(sorted(deictic)), scoped=True))
    return mapping, folds


# ── the document's own entity's date ─────────────────────────────────────────
#
# Each chunk fills the document entity's date property from its own passage, so
# a chunk far from the definition guesses: end to end, a change note whose
# "'Amendment Date' means the date of this Amendment" gave the effective date of
# the contract term a later clause mentions (17/06/2024 instead of the note's own
# 12/12/2025). The date is set once, from the document, instead.


def date_labels(schema: dict) -> list[str]:
    """`document_entity.date_labels`: the defined terms that give the document's
    own date ("Amendment Date", "Effective Date"), in order."""
    setting = schema.get("document_entity")
    labels = setting.get("date_labels") if isinstance(setting, dict) else None
    return [label for label in labels or [] if isinstance(label, str) and label.strip()]


def date_property(schema: dict, entity_class: str) -> str | None:
    """The property of `entity_class` tagged `temporal: valid_from`, if any."""
    decl = (schema.get("entity_types") or {}).get(entity_class) or {}
    for name, prop in (decl.get("properties") or {}).items():
        if isinstance(prop, dict) and prop.get("temporal") == "valid_from":
            return name
    return None


def own_entities(
    entities: list[dict], canonical_names: dict[str, str], schema: dict, *, title: str, filename: str,
) -> dict[str, str]:
    """{class: canonical name} of the document's own entity, per opted-in class
    that has one: the reference-bearing name (after `fold`, all of them map to
    one) used by the most chunks. A document whose filename and title carry no
    reference has none."""
    refs = references(title, filename)
    if not refs:
        return {}
    own = _own_words(title, filename)
    found: dict[str, str] = {}
    for entity_class in document_classes(schema):
        chunks: dict[str, set[str]] = {}
        for entity in entities:
            if entity.get("entity_class") != entity_class:
                continue
            raw = entity.get("name") or ""
            canonical = canonical_names.get(raw) or raw
            if raw and names_document(canonical, refs, own):
                chunks.setdefault(canonical, set()).add(entity.get("chunk_id") or "")
        if chunks:
            found[entity_class] = min(chunks, key=lambda n: (-len(chunks[n]), min(chunks[n]), len(n), n))
    return found


def stamp_own_dates(
    entities: list[dict], properties: list[dict], canonical_names: dict[str, str], owners: dict[str, str],
    schema: dict, date: str,
) -> tuple[list[dict], list[tuple[str, str, str]]]:
    """`properties` with the date property of every observation of the
    document's own entity (`owners`) set to `date`, and what was set as
    (class, name, property). Other entities keep their values. Pure: returns
    new property records; the inputs are not changed."""
    out = [dict(p, properties=dict(p.get("properties") or {})) if isinstance(p, dict) else p for p in properties]
    out_by_id = {p.get("id"): p for p in out if isinstance(p, dict)}
    stamped: list[tuple[str, str, str]] = []
    for entity in entities:
        cls = entity.get("entity_class") or ""
        raw = entity.get("name") or ""
        owner = owners.get(cls)
        prop = date_property(schema, cls)
        if not owner or not prop or (canonical_names.get(raw) or raw) != owner:
            continue
        record = out_by_id.get(entity.get("id"))
        if record is None:
            record = {"id": entity.get("id"), "properties": {}}
            out.append(record)
            out_by_id[entity.get("id")] = record
        record["properties"][prop] = date
        if (cls, owner, prop) not in stamped:
            stamped.append((cls, owner, prop))
    return out, stamped
