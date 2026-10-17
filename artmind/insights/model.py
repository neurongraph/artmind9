"""Insights data model: findings, plan entries and runs (spec 2026-10-17 §3, §9.1)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields

FORMAT = 1
KINDS = ("corpus", "health")


class InsightsError(Exception):
    """A user-facing insights failure: bad config, unreachable graph, unreadable snapshot."""


def _known(cls, data: dict) -> dict:
    names = {f.name for f in fields(cls)}
    return {k: v for k, v in data.items() if k in names}


@dataclass
class Finding:
    key: str
    detector: str
    kind: str
    score: float
    title: str
    subjects: list[dict] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    card: dict = field(default_factory=dict)
    action: str | None = None
    narration: str | None = None
    diff: str = "new"

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"{self.key}: kind {self.kind!r} is not one of {KINDS}")
        if not 0.0 <= self.score <= 1.0:
            raise ValueError(f"{self.key}: score {self.score} is outside [0, 1]")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Finding:
        return cls(**_known(cls, data))


@dataclass
class PlanEntry:
    detector: str
    status: str  # ran | skipped | failed
    target: str | None = None
    reason: str | None = None
    findings: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> PlanEntry:
        return cls(**_known(cls, data))


@dataclass
class Run:
    domain: str
    created_at: str
    as_of: str | None
    fingerprint: dict
    anchors: list[dict]
    profile: dict
    plan: list[PlanEntry]
    findings: list[Finding]
    circles: list[dict]
    diff: dict

    def to_dict(self) -> dict:
        return {
            "format": FORMAT,
            "domain": self.domain,
            "created_at": self.created_at,
            "as_of": self.as_of,
            "fingerprint": self.fingerprint,
            "anchors": self.anchors,
            "profile": self.profile,
            "plan": [p.to_dict() for p in self.plan],
            "findings": [f.to_dict() for f in self.findings],
            "circles": self.circles,
            "diff": self.diff,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Run:
        if data.get("format") != FORMAT:
            raise InsightsError(f"snapshot format {data.get('format')!r} is not {FORMAT}")
        return cls(
            domain=data["domain"],
            created_at=data["created_at"],
            as_of=data.get("as_of"),
            fingerprint=data.get("fingerprint") or {},
            anchors=data.get("anchors") or [],
            profile=data.get("profile") or {},
            plan=[PlanEntry.from_dict(p) for p in data.get("plan") or []],
            findings=[Finding.from_dict(f) for f in data.get("findings") or []],
            circles=data.get("circles") or [],
            diff=data.get("diff") or {},
        )
