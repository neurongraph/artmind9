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


@dataclass(frozen=True)
class Fold:
    entity_class: str
    name: str
    folded: tuple[str, ...]


def fold(
    entities: list[dict], canonical_names: dict[str, str], schema: dict, *, title: str, filename: str,
) -> tuple[dict[str, str], list[Fold]]:
    """`canonical_names` with every name of the document's own entity folded
    into one, per opted-in class, and what was folded. Pure: returns a new
    mapping; the input is not changed. Nothing to fold (no opt-in, no
    reference, fewer than two names) returns the mapping as it was."""
    classes = document_classes(schema)
    refs = references(title, filename)
    if not classes or not refs:
        return canonical_names, []
    own = _own_words(title, filename)
    mapping = dict(canonical_names)
    folds: list[Fold] = []
    for entity_class in classes:
        # raw name -> the chunks it came from, for this class's matching names
        chunks_by_canonical: dict[str, set[str]] = {}
        raw_names: set[str] = set()
        for entity in entities:
            if entity.get("entity_class") != entity_class:
                continue
            raw = entity.get("name") or ""
            canonical = mapping.get(raw) or raw
            if not raw or not (names_document(raw, refs, own) or names_document(canonical, refs, own)):
                continue
            raw_names.add(raw)
            chunks_by_canonical.setdefault(canonical, set()).add(entity.get("chunk_id") or "")
        if len(chunks_by_canonical) < 2:
            continue
        chosen = min(
            chunks_by_canonical,
            key=lambda n: (-len(chunks_by_canonical[n]), min(chunks_by_canonical[n]), len(n), n),
        )
        for raw in raw_names:
            mapping[raw] = chosen
        folds.append(Fold(entity_class, chosen, tuple(sorted(n for n in chunks_by_canonical if n != chosen))))
    return mapping, folds
