"""Tiered expected answers (spec 2026-11-08 §2.5): must / optional / must-not items in a question's evaluation
comment. Untiered text is kept as notes, so old comments still read."""

from __future__ import annotations

import re
from dataclasses import dataclass

# A tier heading REQUIRES a colon ("Must:", "**Must:**", "**Must**:"), so ordinary prose such as
# "must have a figure" stays notes.
_HEAD = re.compile(r"^\s*(?:\*\*)?\s*(must[\s_-]*not|must|optional|notes?)\s*(?:\*\*)?\s*:\s*(?:\*\*)?\s*(.*)$",
                   re.IGNORECASE)
_ITEM = re.compile(r"^\s*[-*]\s+(.*)$")


@dataclass(frozen=True)
class Tiers:
    must: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()
    must_not: tuple[str, ...] = ()
    notes: str = ""

    @property
    def tiered(self) -> bool:
        return bool(self.must or self.optional or self.must_not)


def _key(word: str) -> str:
    w = re.sub(r"[\s_-]+", "", word.casefold())
    return {"must": "must", "mustnot": "must_not", "optional": "optional"}.get(w, "notes")


def _split(text: str) -> list[str]:
    return [p.strip() for p in text.split(";") if p.strip()]


def parse(text: str | None) -> Tiers:
    found = {"must": [], "optional": [], "must_not": [], "notes": []}
    current = "notes"
    for line in (text or "").splitlines():
        head = _HEAD.match(line)
        if head:
            current = _key(head.group(1))
            rest = head.group(2).strip()
            if rest:
                found[current].extend([rest] if current == "notes" else _split(rest))
            continue
        item = _ITEM.match(line)
        if item and current != "notes":
            found[current].extend(_split(item.group(1)))
        elif line.strip():
            found["notes"].append(line.strip())
    return Tiers(tuple(found["must"]), tuple(found["optional"]), tuple(found["must_not"]),
                 "\n".join(found["notes"]))
