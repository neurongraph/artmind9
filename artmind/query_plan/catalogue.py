"""The per-domain catalogue for `query plan` (spec 2026-10-22 §11).

The code lives in `artmind.catalogue` (spec 2026-11-02 §4: the planner, the UI and the
agent all read it); this module re-exports what `query plan` and older imports use.
"""
from __future__ import annotations

from artmind.catalogue.cache import (  # noqa: F401
    DEFAULT_PARTS, FINGERPRINT_TTL_S, MAX_AGE_S, build, fingerprints, get, get_detailed, reset, set_clock,
)
from artmind.catalogue.model import CATALOGUE_FORMAT, DocRecord, DomainCatalogue, NameEntry  # noqa: F401
from artmind.catalogue.parts import NAME_INDEX_MAX, RELATIONS_MAX, _class_descriptions  # noqa: F401
