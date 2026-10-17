"""Detector framework (spec 2026-10-17 §5 steps 2-4).

A detector is a name, a kind (corpus | health), a `plan(ctx)` that returns
targets or a Skip with the reason, and a `run(ctx, target)` that returns
findings. Detectors read the RunContext only and never touch the driver.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from artmind.insights.config import InsightsConfig
from artmind.insights.model import Finding
from artmind.insights.profile import EntityRow, Profile


@dataclass(frozen=True)
class Skip:
    reason: str


@dataclass
class RunContext:
    domain: str
    schema: dict
    config: InsightsConfig
    profile: Profile
    entities: list[EntityRow]
    anchors: set[str]
    as_of: str | None
    memo: dict = field(default_factory=dict)

    def scored_periods(self) -> list[str]:
        return [p for p, n in sorted(self.profile.docs_per_period.items()) if n >= self.config.min_period_docs]

    def temporal(self) -> bool:
        return self.profile.latest_doc is not None and self.profile.default_time_share < 0.5

    def classes_of_kind(self, kind: str) -> list[str]:
        return [name for name, cp in sorted(self.profile.classes.items()) if cp.effective_kind == kind]

    def members(self, cls: str, floor: int | None = None) -> list[EntityRow]:
        floor = self.config.min_evidence_days if floor is None else floor
        return [e for e in self.entities if e.cls == cls and e._id not in self.anchors and len(e.days) >= floor]


@dataclass(frozen=True)
class Detector:
    name: str
    kind: str
    plan: Callable[[RunContext], "list[str] | Skip"]
    run: Callable[[RunContext, str], list[Finding]]


def subject(e: EntityRow) -> dict:
    return {"_id": e._id, "name": e.name, "class": e.cls}


def registry() -> list[Detector]:
    """Every detector in run order. `brokers` must follow `communities` (it reads its circles)."""
    from artmind.insights.detectors import brokers, communities, dormancy, health, salience

    return [
        salience.SALIENCE_TREND, salience.TYPE_TREND, dormancy.DORMANCY,
        communities.COMMUNITIES, brokers.BROKERS,
        health.KIND_MISMATCH, health.VOCABULARY_SPRAWL, health.RELATIONSHIP_USAGE, health.OPEN_CONFLICTS,
    ]
