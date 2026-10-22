"""What every plan classifier takes and returns (spec 2026-10-22 §4.4).

A `Decision` carries both the option text a decision model reads and the
deterministic lexical scores, so the lexical backend can always answer the same
decisions, including as a fallback halfway through a plan.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class Decision:
    name: str
    instructions: str
    options: dict[str, str]
    lexical_scores: dict[str, float] = field(default_factory=dict)
    default: str = "none"
    default_confidence: float = 0.5
    none_mass: float = 1.0
    type: str = "choice"


@dataclass(frozen=True)
class Answer:
    choice: str
    probabilities: dict[str, float]
    confidence: float


class ClassifierUnavailable(Exception):
    """The decision backend cannot answer this call; `reason` goes in the payload."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class Classifier(Protocol):
    backend: str
    model: str | None

    def decide(self, question: str, decisions: list[Decision]) -> dict[str, Answer]: ...
