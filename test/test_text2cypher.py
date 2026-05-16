import json

import pytest

from artmind import text2cypher


FAKE_METADATA = {
    "domain": "fiction",
    "rows": [
        {
            "category": "nodes",
            "name": "PERSON",
            "propertyNames": ["name", "domain", "description"],
            "distinctTypes": ["PERSON"],
            "connections": None,
        },
        {
            "category": "relationships",
            "name": "KNOWS",
            "propertyNames": ["since"],
            "distinctTypes": None,
            "connections": [{"from": ["PERSON"], "to": ["PERSON"]}],
        },
    ],
}

FAKE_LISTING = {
    "domain": "fiction",
    "rows": [
        {
            "label": "PERSON",
            "typeGroups": [
                {"type": "PERSON", "names": ["Sherlock Holmes", "Dr. Watson"]},
            ],
        },
    ],
}


def test_build_prompt_includes_schema_and_entities():
    schema_info = text2cypher._schema_summary(FAKE_METADATA)
    entities_info = text2cypher._entities_summary(FAKE_LISTING)

    prompt = text2cypher.build_text2cypher_prompt(
        question="How many persons are there?",
        schema_info=schema_info,
        entities_info=entities_info,
        domain="fiction",
    )

    assert "PERSON" in prompt
    assert "Sherlock Holmes" in prompt
    assert "KNOWS" in prompt
    assert "fiction" in prompt
    assert "How many persons are there?" in prompt


def test_validate_read_only_accepts_match_return():
    text2cypher.validate_read_only("MATCH (n) RETURN count(n)")


@pytest.mark.parametrize(
    "cypher",
    [
        "CREATE (n:Person {name: 'x'})",
        "MATCH (n) DELETE n",
        "MATCH (n) DETACH DELETE n",
        "MATCH (n) SET n.name = 'x'",
        "MATCH (n) REMOVE n.name",
        "MERGE (n:Person {name: 'x'})",
        "DROP INDEX foo",
    ],
)
def test_validate_read_only_rejects_write_operations(cypher):
    with pytest.raises(ValueError, match="write operation"):
        text2cypher.validate_read_only(cypher)


def test_generate_cypher_returns_cypher_and_params(monkeypatch):
    llm_response = json.dumps({
        "cypher": "MATCH (n:PERSON) WHERE n.domain = $domain RETURN count(n) AS total",
        "parameters": {"domain": "fiction"},
    })

    monkeypatch.setattr(text2cypher, "graph_metadata", lambda domain: FAKE_METADATA)
    monkeypatch.setattr(text2cypher, "entity_listing", lambda domain: FAKE_LISTING)
    monkeypatch.setattr(text2cypher, "call_llm", lambda model, prompt: llm_response)

    result = text2cypher.generate_cypher("How many persons?", "fiction", model="test-model")

    assert "MATCH" in result["cypher"]
    assert result["parameters"]["domain"] == "fiction"


def test_generate_cypher_rejects_write_cypher(monkeypatch):
    llm_response = json.dumps({
        "cypher": "CREATE (n:Person {name: 'Evil'})",
        "parameters": {"domain": "fiction"},
    })

    monkeypatch.setattr(text2cypher, "graph_metadata", lambda domain: FAKE_METADATA)
    monkeypatch.setattr(text2cypher, "entity_listing", lambda domain: FAKE_LISTING)
    monkeypatch.setattr(text2cypher, "call_llm", lambda model, prompt: llm_response)

    with pytest.raises(ValueError, match="write operation"):
        text2cypher.generate_cypher("Create a person", "fiction", model="test-model")


def test_generate_cypher_raises_on_empty_cypher(monkeypatch):
    llm_response = json.dumps({"cypher": "", "parameters": {}})

    monkeypatch.setattr(text2cypher, "graph_metadata", lambda domain: FAKE_METADATA)
    monkeypatch.setattr(text2cypher, "entity_listing", lambda domain: FAKE_LISTING)
    monkeypatch.setattr(text2cypher, "call_llm", lambda model, prompt: llm_response)

    with pytest.raises(ValueError, match="did not return"):
        text2cypher.generate_cypher("Something", "fiction", model="test-model")


def test_execute_text2cypher_dry_run_skips_execution(monkeypatch):
    llm_response = json.dumps({
        "cypher": "MATCH (n:PERSON) WHERE n.domain = $domain RETURN count(n) AS total",
        "parameters": {"domain": "fiction"},
    })

    monkeypatch.setattr(text2cypher, "graph_metadata", lambda domain: FAKE_METADATA)
    monkeypatch.setattr(text2cypher, "entity_listing", lambda domain: FAKE_LISTING)
    monkeypatch.setattr(text2cypher, "call_llm", lambda model, prompt: llm_response)

    # _run_read_query should NOT be called in dry-run mode
    def fail_if_called(cypher, params):
        raise AssertionError("_run_read_query should not be called in dry-run mode")

    monkeypatch.setattr(text2cypher, "_run_read_query", fail_if_called)

    result = text2cypher.execute_text2cypher(
        "How many persons?", "fiction", model="test-model", dry_run=True
    )

    assert result["dry_run"] is True
    assert result["rows"] == []
    assert result["command"] == "text2cypher"
    assert "MATCH" in result["generated_cypher"]


def test_execute_text2cypher_runs_query(monkeypatch):
    llm_response = json.dumps({
        "cypher": "MATCH (n:PERSON) WHERE n.domain = $domain RETURN count(n) AS total",
        "parameters": {"domain": "fiction"},
    })

    monkeypatch.setattr(text2cypher, "graph_metadata", lambda domain: FAKE_METADATA)
    monkeypatch.setattr(text2cypher, "entity_listing", lambda domain: FAKE_LISTING)
    monkeypatch.setattr(text2cypher, "call_llm", lambda model, prompt: llm_response)
    monkeypatch.setattr(
        text2cypher, "_run_read_query", lambda cypher, params: [{"total": 42}]
    )

    result = text2cypher.execute_text2cypher(
        "How many persons?", "fiction", model="test-model"
    )

    assert result["rows"] == [{"total": 42}]
    assert result["query_type"] == "graph"
    assert result["command"] == "text2cypher"


def test_schema_summary_handles_empty_metadata():
    assert "no schema" in text2cypher._schema_summary({"rows": []})


def test_entities_summary_handles_empty_listing():
    assert "no entities" in text2cypher._entities_summary({"rows": []})


def test_entities_summary_truncates_large_name_lists():
    names = [f"Entity_{i}" for i in range(30)]
    listing = {
        "rows": [
            {
                "label": "THING",
                "typeGroups": [{"type": "THING", "names": names}],
            }
        ]
    }
    summary = text2cypher._entities_summary(listing)
    assert "30 total" in summary
    assert "Entity_0" in summary
    assert "Entity_25" not in summary
