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
