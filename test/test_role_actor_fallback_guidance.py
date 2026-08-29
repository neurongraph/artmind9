"""Regression for neurongraph/artmind9#14.

`ROLE_ACTOR`'s extraction guidance had no fallback for "no specific role
named," so the extractor wrote the literal class label ("Role Actor") as the
entity name whenever a document referenced a responsible role generically.
Because `aggregate_key()` identifies entities by `(name, entity_class,
domain)`, every such chunk collapsed onto one identical entity, aggregating
unrelated facts under a generic identity.

This reads the shipped schema — the source of truth in
`artmind/domains/schemas/`, not the run-folder copy — so a future edit that
drops the fallback guidance fails here rather than surfacing as another
generic "Role Actor" entity six weeks later.
"""
import yaml

from paths import PROJECT_ROOT

SCHEMA_PATH = PROJECT_ROOT / "artmind" / "domains" / "schemas" / "banking.sop_guides_schema.yaml"


def _role_actor_guidance() -> str:
    schema = yaml.safe_load(SCHEMA_PATH.read_text(encoding="utf-8"))
    return (schema["entity_types"]["ROLE_ACTOR"].get("guidance") or "")


def test_guidance_instructs_against_the_bare_class_label_as_a_fallback_name():
    guidance = _role_actor_guidance()
    assert "Role Actor" not in [line.strip() for line in guidance.splitlines()], (
        "guidance should describe the bad fallback, not silently demonstrate it "
        "as a RIGHT example"
    )
    assert any(
        keyword in guidance.lower()
        for keyword in ("does not name a specific role", "no specific role", "no role named")
    ), "ROLE_ACTOR guidance must tell the extractor what to do when no role is named"


def test_guidance_shows_the_live_bug_as_a_WRONG_example():
    guidance = _role_actor_guidance()
    assert 'WRONG:' in guidance and '"Role Actor"' in guidance, (
        "the live defect (an entity literally named 'Role Actor') should be the "
        "worked WRONG example, matching this schema's RIGHT/WRONG convention"
    )
