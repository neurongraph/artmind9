"""Plan classifiers: lexical rules always; a decision model when configured (spec 2026-10-22 §4.4)."""

from __future__ import annotations

from artmind.query_plan.classify.base import Answer, Classifier, ClassifierUnavailable, Decision
from artmind.query_plan.classify.lexical import LexicalClassifier

__all__ = ["Answer", "Classifier", "ClassifierUnavailable", "Decision", "LexicalClassifier", "select_classifier"]


def select_classifier(option: str, domain_choice: str, model_override: str | None) -> tuple[Classifier, str | None]:
    """(classifier, reason it is lexical) for `--classifier` and the domain config.
    The decision backend arrives in Task 16; until then only lexical exists."""
    if option == "decision":
        raise ValueError("--classifier decision needs ARTMIND_DECISION_URL and ARTMIND_DECISION_MODEL")
    if option == "lexical":
        return LexicalClassifier(), None
    return LexicalClassifier(), ("domain_config" if domain_choice == "lexical" else "no_endpoint")
