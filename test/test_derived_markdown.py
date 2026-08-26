"""Tests for artmind.derived_markdown: the pure promotion decision
(docs/document-identity.md, "Derived-markdown promotion")."""

from pathlib import Path

from artmind.derived_markdown import (
    PromotionDecision,
    decide,
    derived_markdown_path,
    is_promoted,
    markdown_was_edited,
)
from artmind.document_identity import compute_content_sha256


def test_derived_markdown_path_is_domain_and_stem_scoped():
    vault = Path("/vault")
    assert derived_markdown_path(vault, "banking.reference", "deck") == (
        vault / "_derived" / "banking.reference" / "deck.md"
    )


def test_is_promoted_true_only_when_source_type_is_md():
    assert is_promoted({"_source_type": "md"}) is True
    assert is_promoted({"_source_type": "pptx"}) is False
    assert is_promoted({}) is False


def test_markdown_was_edited_false_when_hash_matches():
    body = "hello world"
    assert markdown_was_edited(body, compute_content_sha256(body)) is False


def test_markdown_was_edited_true_when_hash_differs():
    assert markdown_was_edited("edited body", compute_content_sha256("original body")) is True


def test_markdown_was_edited_true_when_fingerprint_missing():
    # No _derived_sha256 to compare against -- conservative direction: treat
    # as edited so it routes to promote, never a silent overwrite.
    assert markdown_was_edited("some body", None) is True


def test_decide_no_op_when_nothing_changed():
    result = decide(markdown_edited=False, binary_changed=False)
    assert result == PromotionDecision("no_op", markdown_edited=False, binary_changed=False)


def test_decide_convert_when_only_binary_changed():
    result = decide(markdown_edited=False, binary_changed=True)
    assert result.action == "convert"


def test_decide_promote_when_only_markdown_edited():
    result = decide(markdown_edited=True, binary_changed=False)
    assert result.action == "promote"


def test_decide_collision_when_both_changed():
    result = decide(markdown_edited=True, binary_changed=True)
    assert result.action == "collision"
