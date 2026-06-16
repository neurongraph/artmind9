import jq
from artmind.wizard_commands import COMMANDS, INFO_NODES, REQUIRED_ARG_KEYS, REQUIRED_KEYS, STAGES


def test_all_stages_present():
    stage_nums = {cmd["stage"] for cmd in COMMANDS.values()}
    assert stage_nums == {1, 2, 3, 4, 5, 6, 7}


def test_all_commands_have_required_keys():
    for cmd_id, cmd in COMMANDS.items():
        missing = REQUIRED_KEYS - set(cmd.keys())
        assert not missing, f"{cmd_id} missing keys: {missing}"


def test_all_args_have_required_keys():
    for cmd_id, cmd in COMMANDS.items():
        for arg in cmd["args"]:
            missing = REQUIRED_ARG_KEYS - set(arg.keys())
            assert not missing, f"{cmd_id} arg '{arg.get('flag')}' missing keys: {missing}"


def test_jq_expressions_are_valid():
    for cmd_id, cmd in COMMANDS.items():
        for view_name, expr in cmd["views"].items():
            try:
                jq.compile(expr)
            except Exception as e:
                raise AssertionError(f"{cmd_id} view '{view_name}' has invalid jq: {e}")


# Sample CLI output envelopes, shaped to match what each graph_query/vector_query
# pattern actually returns (the {domain, ..., "rows": [...]} envelope with
# pattern-specific nesting under each row) — not just a bare array. Views were
# previously written against a bare-array assumption and compiled fine but blew
# up at runtime ("Cannot index string with string ...") once run against real
# output, since `test_jq_expressions_are_valid` only checks that the jq parses.
SAMPLE_QUERY_OUTPUT = {
    "query.metadata": {
        "rows": [
            {"category": "nodes", "name": "CHARACTER", "propertyNames": ["name"]},
            {"category": "relationships", "name": "KNOWS", "propertyNames": []},
        ]
    },
    "query.pattern1": {
        "rows": [{"entityData": {"name": "Holmes", "entity_class": "CHARACTER"}}]
    },
    "query.pattern2": {
        "rows": [{"entityData": {"name": "Holmes", "description": "A detective"}}]
    },
    "query.pattern3": {
        "rows": [{
            "entityData": {"name": "Holmes"},
            "connections": [{"type": "KNOWS", "target": {"name": "Watson"}}, None],
        }]
    },
    "query.pattern4": {
        "rows": [{
            "entityData": {"name": "Holmes"},
            "connections": [
                {"rel_type": "KNOWS", "connected_to": {"label": ["CHARACTER"], "data": {"name": "Watson"}}},
                None,
            ],
        }]
    },
    "query.pattern5": {
        "rows": [{
            "interleavedPath": [
                {"label": ["CHARACTER"], "data": {"name": "Holmes"}},
                {"type": "KNOWS", "data": {}},
                {"labels": ["CHARACTER"], "data": {"name": "Watson"}},
            ]
        }]
    },
    "query.pattern6": {
        "rows": [{"relType": "KNOWS", "fromEntity": "Holmes", "toEntity": "Watson"}]
    },
    "query.pattern7": {
        "rows": [{"entityData": {"name": "Baker Street"}}]
    },
    "query.pattern8": {
        "rows": [{"entityData": {"name": "221B"}, "relType": "LOCATED_AT"}]
    },
    "query.pattern9": {
        "rows": [{"entityData": {"name": "Holmes", "degree": 5}}]
    },
    "query.pattern10": {
        "rows": [{"document": {"name": "doc1"}, "chunk": {"text": "Some text"}}]
    },
    "query.vector-text": {
        "rows": [
            {"score": 0.9, "chunk": {"text": "Some excerpt"}, "source_type": "document"},
            {"score": 0.5, "chat": {"raw_text": "chat text"}, "source_type": "user_chat"},
        ]
    },
    "query.entity-resolve": {
        "rows": [{"score": 0.8, "entity": {"name": "Holmes", "entity_class": "CHARACTER"}}]
    },
}


def test_query_jq_views_match_real_output_shape():
    """Views must run cleanly against the actual {"rows": [...]} envelope shape.

    test_jq_expressions_are_valid only checks the jq compiles; a filter
    written for a bare-array shape compiles fine but errors at runtime against
    the real nested envelope. Run each view against a representative sample
    and require a non-error result.
    """
    for cmd_id, sample in SAMPLE_QUERY_OUTPUT.items():
        views = COMMANDS[cmd_id]["views"]
        for view_name, expr in views.items():
            try:
                result = jq.compile(expr).input(sample).all()
            except Exception as e:
                raise AssertionError(f"{cmd_id} view '{view_name}' raised on sample data: {e}")
            assert result, f"{cmd_id} view '{view_name}' produced empty output on sample data"


def test_stages_list_covers_all_stage_nums():
    stage_nums_in_stages = {s["num"] for s in STAGES}
    assert stage_nums_in_stages == {1, 2, 3, 4, 5, 6, 7}


def test_setup_command_has_no_required_args():
    setup = COMMANDS["setup"]
    required_args = [a for a in setup["args"] if a["required"]]
    assert required_args == []


def test_info_nodes_have_stage_label_description():
    for info_id, info in INFO_NODES.items():
        for key in ("stage", "label", "description"):
            assert key in info, f"INFO_NODES['{info_id}'] missing '{key}'"
