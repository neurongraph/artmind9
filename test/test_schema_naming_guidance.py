"""A recurrent class's guidance must not fight the recurrent naming rule.

`meta.yaml` declares that a recurrent class's name must never embed a
measurement or a date — that is what makes the same thing recognisable in the
next document, and it is what the aggregate key depends on. Phase 3 started
injecting that rule into the extraction prompt.

But a class's own `guidance` is rendered **after** the rule, so it gets the
last word. Two shipped schemas were instructing the extractor to do exactly the
opposite, with worked `RIGHT:` examples:

    RATE_ENTRY names include product, tier, rate value, and effective date.
    RIGHT: { "name": "SmartSaver Account Tier 1 Rate — 4.50% AER (...), effective 2026-01-15" }

The extractor followed them faithfully, and the live vertical-slice run produced
names carrying rates and dates that the key function then had to strip back off.
Worse, one garbled extraction ("Tier 2 Rate — 5.25% AER", which is actually
SmartSaver *Plus*) folded into the Tier 2 key and raised a same-instant
conflict on `rate_value`.

These tests read the shipped schemas — the source of truth in
`artmind/domains/schemas/`, not the run-folder copies — so a schema edit that
reintroduces the contradiction fails here rather than in a corpus six weeks
later.
"""
import re

import pytest
import yaml

from paths import PROJECT_ROOT

SCHEMA_DIR = PROJECT_ROOT / "artmind" / "domains" / "schemas"

# Guidance that tells the extractor to put a value or a date IN THE NAME.
_NAME_CARRIES_VALUE = re.compile(
    r"name[s]?\b[^.\n]*\b(include|includes|including|with|carry|carries|contain|contains)\b"
    r"[^.\n]*(rate value|actual value|threshold|\bvalue\b|\bdate\b|amount|percentage|figure)",
    re.IGNORECASE,
)
# A worked example whose "name" field carries a number — the most persuasive
# form of the same instruction, and the one a model copies most readily.
_EXAMPLE_NAME_WITH_NUMBER = re.compile(r'"name"\s*:\s*"[^"]*\d[^"]*"')


def _recurrent_classes():
    for path in sorted(SCHEMA_DIR.glob("*_schema.yaml")):
        schema = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for cls, decl in (schema.get("entity_types") or {}).items():
            if (decl or {}).get("kind") == "recurrent":
                yield path.name, cls, (decl.get("guidance") or "")


RECURRENT = list(_recurrent_classes())


def test_the_corpus_actually_has_recurrent_classes_to_check():
    """Guard the guard: a globbing or `kind` regression would make every test
    below vacuously pass."""
    assert len(RECURRENT) > 10, f"only {len(RECURRENT)} recurrent classes found"


@pytest.mark.parametrize(
    "filename,cls,guidance",
    [pytest.param(f, c, g, id=f"{f}::{c}") for f, c, g in RECURRENT],
)
def test_recurrent_guidance_does_not_tell_the_extractor_to_name_things_by_value(
    filename, cls, guidance
):
    for line in guidance.splitlines():
        if line.strip().upper().startswith("WRONG:"):
            continue  # a counter-example is the point
        match = _NAME_CARRIES_VALUE.search(line)
        assert not match, (
            f"{filename} :: {cls} (kind: recurrent) instructs the extractor to put a "
            f"value or date in the entity NAME, contradicting meta.yaml's recurrent "
            f"naming rule:\n    {line.strip()}\n"
            f"Values belong in properties — a name carrying one cannot be recognised "
            f"as the same thing in the next document."
        )


@pytest.mark.parametrize(
    "filename,cls,guidance",
    [pytest.param(f, c, g, id=f"{f}::{c}") for f, c, g in RECURRENT],
)
def test_no_recurrent_RIGHT_example_shows_a_name_carrying_a_number(filename, cls, guidance):
    for line in guidance.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("WRONG:"):
            continue
        found = _EXAMPLE_NAME_WITH_NUMBER.search(stripped)
        if not found:
            continue
        # "Tier 2", "Basel III", "SAV-001" are identity, not measurement.
        # A percent sign, a currency symbol or an ISO date is not.
        if re.search(r"[%£$€]|\d{4}-\d{2}-\d{2}", found.group(0)):
            raise AssertionError(
                f"{filename} :: {cls} (kind: recurrent) shows a worked example whose "
                f"name embeds a measurement or a date:\n    {stripped}\n"
                f"Models copy examples more readily than they follow prose."
            )


def test_the_recurrent_rule_still_reaches_the_prompt_ahead_of_class_guidance():
    """The rule is rendered first so a schema can still say something more
    specific — which is exactly why the guidance has to agree with it."""
    from artmind.prompt_builder import assemble_entities_prompt

    schema = yaml.safe_load(
        (SCHEMA_DIR / "banking.reference_schema.yaml").read_text(encoding="utf-8")
    )
    prompt = assemble_entities_prompt(schema)
    assert "NEVER embed a measurement" in prompt

    rule_at = prompt.index("NEVER embed a measurement")
    guidance_at = prompt.index("identify the TIER, never its current rate")
    assert rule_at < guidance_at, "the kind rule must precede the class's own guidance"
