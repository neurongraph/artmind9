"""The admin-ui CLI guide (artmind/cli_guide.py) and its coverage guarantee."""

from fastapi.testclient import TestClient

from artmind.cli_guide import build_sections, click, cli, find_undocumented_commands, render_fragment
from artmind.webui.app import create_app


def test_every_command_is_routed_into_the_guide() -> None:
    """Fail if a command exists that COMMAND_GROUPS doesn't route anywhere.

    `build_sections()` only walks paths reachable from
    `click.rich_click.COMMAND_GROUPS`, so a command added to cli.py without a
    matching entry there is silently absent from both the guide and the
    grouped `--help` output. This is what caught `db`, `query text2sql`,
    `query resolve-key` and the whole `snapshot` group going missing.
    """
    missing = find_undocumented_commands()
    assert not missing, (
        "These commands aren't reachable from any COMMAND_GROUPS entry and "
        "would vanish from the CLI guide:\n  "
        + "\n  ".join(missing)
        + "\n\nAdd them to click.rich_click.COMMAND_GROUPS in artmind/cli.py."
    )


def test_fragment_is_a_fragment_not_a_page() -> None:
    """The guide must carry no page chrome, styling, or behaviour of its own.

    It is injected into the dashboard's DOM, so a stray <style> would leak
    across the whole admin-ui (both define --bg/--text/--accent on :root) and
    a <script> would never execute via innerHTML in the first place.
    """
    fragment = render_fragment()
    for forbidden in ("<!DOCTYPE", "<html", "<head", "<body", "<style", "<script", ":root"):
        assert forbidden not in fragment, f"fragment must not contain {forbidden!r}"


def test_runnable_groups_get_their_own_card() -> None:
    """A group with invoke_without_command=True is a command, not just a namespace.

    `artmind db mappings TABLE` lists mappings as its default action and owns
    --acceptProposed, which appears on none of its subcommands. Treating every
    click.Group as a pure namespace left it — and that flag — undocumented.
    """
    runnable = [
        path
        for path, cmd in _walk_groups(cli, "artmind")
        if cmd.invoke_without_command
    ]
    assert "artmind db mappings" in runnable, "test's own premise broke — check cli.py"

    fragment = render_fragment()
    for path in runnable:
        assert f">{path}<" in fragment, f"runnable group {path} has no card"


def _walk_groups(cmd, path):
    if isinstance(cmd, click.Group):
        yield path, cmd
        for name, sub in cmd.commands.items():
            yield from _walk_groups(sub, f"{path} {name}")


def test_fragment_covers_the_command_tree() -> None:
    fragment = render_fragment()
    for path in ("artmind db sql", "artmind query text2sql", "artmind snapshot create"):
        assert path in fragment, f"{path} missing from the guide"
    # One card per leaf command.
    documented = sum(len(items) for s in build_sections() for _, items in s["panels"])
    assert fragment.count('class="cg-card"') == documented


def test_cli_guide_route_serves_the_fragment() -> None:
    client = TestClient(create_app(admin_routes=True))
    response = client.get("/api/cli-guide")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "artmind db sql" in response.text


def test_cli_guide_route_is_admin_only() -> None:
    """The chat UI is the end-user surface — operator tooling stays off it."""
    client = TestClient(create_app())
    assert client.get("/api/cli-guide").status_code == 404
