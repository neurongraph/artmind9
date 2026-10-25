"""The summary prompt is built from each shipped schema alone (spec §4): it names that
schema's subject and classes, for shapes as different as a contract, a general note and a
banking reference. Asserted on the prompt text the summary call would send."""
from pathlib import Path

import pytest
import yaml

import artmind.document_context as dc

SCHEMAS = Path(dc.__file__).parent / "domains" / "schemas"


def _schema(name):
    return yaml.safe_load((SCHEMAS / f"{name}_schema.yaml").read_text(encoding="utf-8")) or {}


@pytest.mark.parametrize("name", ["contracts", "general", "banking.reference"])
def test_the_summary_prompt_names_the_schemas_own_subject_and_classes(name):
    schema = _schema(name)
    prompt = dc.summary_prompt(schema, "SAMPLE", focus="")

    classes = list(schema.get("entity_types") or {})
    assert classes, f"{name} declares classes"
    assert all(cls in prompt for cls in classes), "every class is named"
    assert "SAMPLE" in prompt and '"summary"' in prompt and '"identifiers"' in prompt


@pytest.mark.parametrize("name", ["contracts", "general", "banking.reference"])
def test_the_subject_is_the_schemas_own_one_line_subject(name):
    schema = _schema(name)
    subject = " ".join(str(schema.get("subject") or "").split())
    prompt = dc.summary_prompt(schema, "s", focus="")
    if subject:
        assert f"about {subject}." in prompt or subject in prompt


def test_every_shipped_schema_yields_a_prompt_naming_its_classes():
    checked = 0
    for path in sorted(SCHEMAS.glob("*_schema.yaml")):
        schema = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        classes = list(schema.get("entity_types") or {})
        if not classes:
            continue
        prompt = dc.summary_prompt(schema, "s", focus="")
        assert classes[0] in prompt, path.name
        checked += 1
    assert checked >= 15, "the shipped schemas were all read"


def test_every_shipped_schema_yields_a_bounded_prompt():
    """A prompt is the instructions plus the class list; the sample is added at call time and
    capped separately. It must not grow with a schema's descriptions (see dc._line / _one_sentence)."""
    for path in sorted(SCHEMAS.glob("*_schema.yaml")):
        schema = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not schema.get("entity_types"):
            continue
        prompt = dc.summary_prompt(schema, "", focus="")
        assert len(prompt) < 12000, f"{path.name}: {len(prompt)} characters of instructions"
