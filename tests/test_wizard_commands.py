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
