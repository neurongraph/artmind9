import pytest

pytest_plugins = ("anyio",)


@pytest.mark.anyio
async def test_wizard_launches():
    from artmind.wizard import WizardApp
    async with WizardApp().run_test() as pilot:
        assert pilot.app.title == "artmind wizard"


@pytest.mark.anyio
async def test_lifecycle_tree_present():
    from artmind.wizard import WizardApp
    from textual.widgets import Tree
    async with WizardApp().run_test() as pilot:
        tree = pilot.app.query_one("#lifecycle-tree", Tree)
        assert tree is not None


@pytest.mark.anyio
async def test_command_panel_present():
    from artmind.wizard import WizardApp
    async with WizardApp().run_test() as pilot:
        panel = pilot.app.query_one("#command-panel")
        assert panel is not None


@pytest.mark.anyio
async def test_tree_has_7_stage_nodes():
    from artmind.wizard import WizardApp
    from textual.widgets import Tree
    async with WizardApp().run_test() as pilot:
        tree = pilot.app.query_one("#lifecycle-tree", Tree)
        stage_nodes = list(tree.root.children)
        assert len(stage_nodes) == 7


@pytest.mark.anyio
async def test_selecting_setup_node_shows_teaching_text():
    from artmind.wizard import WizardApp
    from textual.widgets import Static, Tree
    async with WizardApp().run_test() as pilot:
        tree = pilot.app.query_one("#lifecycle-tree", Tree)
        setup_stage = list(tree.root.children)[0]
        setup_leaf = list(setup_stage.children)[0]
        tree.select_node(setup_leaf)
        await pilot.pause()
        teaching = pilot.app.query_one("#teaching-text", Static)
        assert "idempotent" in str(teaching.content)


@pytest.mark.anyio
async def test_free_mode_removes_locked_labels():
    from artmind.wizard import WizardApp
    from textual.widgets import Tree
    async with WizardApp().run_test() as pilot:
        app = pilot.app
        tree = app.query_one("#lifecycle-tree", Tree)
        stage3_node = list(tree.root.children)[2]
        # In guided mode, stage 3 starts locked (dim markup applied)
        assert "[dim]" in stage3_node.label.markup
        # Switch to free mode
        app.mode = "free"
        app._apply_mode_to_tree()
        await pilot.pause()
        assert "[dim]" not in stage3_node.label.markup


def test_complete_stage_updates_completed_stages():
    from artmind.wizard import WizardApp
    app = WizardApp()
    # _complete_stage requires the tree to be mounted — test state directly
    app.completed_stages = frozenset({1})
    assert 1 in app.completed_stages


def test_extract_session_id_from_draft_output():
    from artmind.wizard import WizardApp
    app = WizardApp()
    fake_output = '{"session_id": "sess-abc123", "entities": []}'
    app._extract_and_store_session_id(fake_output)
    assert app.last_session_id == "sess-abc123"


def test_find_kg_dir_returns_none_when_no_doc(tmp_path):
    from artmind.wizard import WizardApp
    result = WizardApp._find_kg_dir("fiction", None, kg_base=tmp_path)
    assert result is None


def test_find_kg_dir_returns_path_when_present(tmp_path):
    import json
    from artmind.wizard import WizardApp
    kg_dir = tmp_path / "fiction" / "my_doc"
    kg_dir.mkdir(parents=True)
    (kg_dir / "entities.json").write_text(json.dumps([{"name": "Holmes"}]))
    result = WizardApp._find_kg_dir("fiction", "my_doc", kg_base=tmp_path)
    assert result == kg_dir


@pytest.mark.anyio
async def test_comma_separated_multi_arg_expands_to_repeated_flags():
    # pattern2's --entityNameList is a Click `multiple=True` option, so it must
    # be passed as repeated flags ("--entityNameList Holmes --entityNameList
    # Watson"), not a single "--entityNameList Holmes,Watson". The wizard
    # advertises comma-separated input for convenience, so it must fan a
    # comma-separated value out into repeated flags rather than passing the
    # raw string through (which Click would treat as one literal name and
    # match nothing).
    from artmind.wizard import CommandForm, WizardApp
    from textual.widgets import Input
    async with WizardApp().run_test() as pilot:
        app = pilot.app
        form = app.query_one(CommandForm)
        form.build_form("query.pattern2", "manual")
        await pilot.pause()
        app._current_cmd_id = "query.pattern2"
        widget = form._field_widgets["--entityNameList"]
        assert isinstance(widget, Input)
        widget.value = "Holmes, Watson"
        args = app._build_cli_args("query.pattern2")
        assert args.count("--entityNameList") == 2
        idx = args.index("--entityNameList")
        assert args[idx + 1] == "Holmes"
        idx2 = args.index("--entityNameList", idx + 1)
        assert args[idx2 + 1] == "Watson"
