"""Contract tests over the Click tree (spec 2026-11-02-cli-uniformity-design.md §4).

They walk the tree the way test_cli_guide.py does, so a new command cannot
regress the conventions in artmind/cli_conventions.py.
"""

from pathlib import Path

import click
import pytest

from artmind import cli_conventions as cc
from artmind.cli import cli

ROOT = Path(__file__).resolve().parent.parent


def _leaves(cmd: click.Command = cli, path: tuple[str, ...] = ()):
    """Every runnable command: leaves, plus groups that run on their own."""
    if isinstance(cmd, click.Group):
        if cmd.invoke_without_command and path:
            yield " ".join(path), cmd
        for name, sub in sorted(cmd.commands.items()):
            yield from _leaves(sub, (*path, name))
    else:
        yield " ".join(path), cmd


def _long_opts(cmd: click.Command) -> list[str]:
    return [o for p in cmd.params if isinstance(p, click.Option) for o in p.opts if o.startswith("--")]


def _hidden_opts(cmd: click.Command) -> set[str]:
    return {o for p in cmd.params if isinstance(p, click.Option) and p.hidden for o in p.opts}


def _check(allowed: set[str], found: set[str], what: str) -> None:
    """`found` must equal `allowed`; an allow-list entry that no longer applies fails too."""
    unexpected = sorted(found - allowed)
    assert not unexpected, f"{what}:\n  " + "\n  ".join(unexpected)
    stale = sorted(allowed - found)
    assert not stale, "no longer applies -- remove from the allow-list:\n  " + "\n  ".join(stale)


def test_every_command_takes_compact() -> None:
    exempt = cc.SERVER_COMMANDS | cc.INTERACTIVE_COMMANDS
    failing = {p for p, c in _leaves() if p not in exempt and "--compact" not in _long_opts(c)}
    _check(set(), failing, "commands without --compact")


def test_no_visible_option_is_kebab_case() -> None:
    failing = {
        p for p, c in _leaves()
        if any("-" in o[2:] for o in _long_opts(c) if o not in _hidden_opts(c))
    }
    _check(set(), failing, "commands with a kebab-case option")


def test_write_commands_exist_and_take_dry_run() -> None:
    paths = dict(_leaves())
    missing = sorted(set(cc.WRITE_COMMANDS) - set(paths))
    assert not missing, f"WRITE_COMMANDS names commands not in the tree: {missing}"
    failing = {p for p in cc.WRITE_COMMANDS if "--dryRun" not in _long_opts(paths[p])}
    _check(set(), failing, "write commands without --dryRun")


def _effect_test_exists(path: str) -> bool:
    """Naming convention: tier-1 command `a b-c` is covered by test/test_dry_run_a_b_c.py."""
    return (ROOT / "test" / f"test_dry_run_{path.replace(' ', '_').replace('-', '_')}.py").exists()


def test_tier_one_commands_have_an_effect_test() -> None:
    failing = {p for p, tier in cc.WRITE_COMMANDS.items() if tier == 1 and not _effect_test_exists(p)}
    _check(set(), failing, "tier-1 commands without test/test_dry_run_<command>.py")


# ── a write command is declared (spec §4 item 5) ──────────────────────────

# Names that mean "this callback writes the graph, staging or vault files". The check is
# approximate: it reads each command's own source, so a write hidden in a helper is not seen
# here (the tier lists and review catch those); a false positive goes in _READ_ONLY with the reason.
_WRITE_HELPERS = (
    "_commit_document_tx", "lifecycle.", "sameas.", "topics.store", "neo4j_session(",
    "write_text(", "write_bytes(", "shutil.", ".unlink(", "rmtree(", "_atomic_write",
)
_READ_ONLY: dict[str, str] = {
    "insights same-as": "--export writes one review file the operator names; the graph is only read",
    "sameas list": "reads the review queue",
    "sameas propose": "writes the proposal queue file only; no graph, staging or vault note",
}


def test_a_command_that_writes_is_declared() -> None:
    import inspect

    found = set()
    for path, command in _leaves():
        if path in cc.WRITE_COMMANDS or command.callback is None:
            continue
        try:
            source = inspect.getsource(command.callback)
        except (OSError, TypeError):
            continue
        if any(helper in source for helper in _WRITE_HELPERS):
            found.add(path)
    _check(set(_READ_ONLY), found, "commands that write but are in neither WRITE_COMMANDS nor _READ_ONLY")


# ── callers use the new spellings (spec §3.3) ──────────────────────────────

_CALLER_GLOBS = (
    "artmind/skills/**/*.md", "justfile", "artmind/query_plan/routes.yaml",
    "obsidian/artmind-obsidian/src/**/*.ts", "artmind/webui/**/*.py", "artmind/webui/**/*.js",
    "artmind/webui/**/*.html", "CLAUDE.md", "docs/INSTALL.md", "docs/vault.md",
    "docs/USER_GUIDE_TWO_LAPTOPS.md", "docs/CAPABILITIES.md",
)


def _deprecated_uses() -> list[str]:
    import re

    found = []
    for pattern in _CALLER_GLOBS:
        for f in sorted(ROOT.glob(pattern)):
            if not f.is_file():
                continue
            text = f.read_text(encoding="utf-8", errors="replace")
            # `git push --dry-run` and the like are git's own flags, not artmind's.
            lines = [ln for ln in text.splitlines() if not re.search(r"\bgit\b", ln)]
            text = "\n".join(lines)
            for old in cc.DEPRECATED_ALIASES:
                if re.search(re.escape(old) + r"(?![A-Za-z0-9-])", text):
                    found.append(f"{f.relative_to(ROOT)}: {old}")
    return found


def test_no_caller_uses_a_deprecated_spelling() -> None:
    assert not _deprecated_uses()


# ── tier-2 --dryRun refuses until it is built ──────────────────────────────


@pytest.mark.parametrize("path", sorted(p for p, tier in cc.WRITE_COMMANDS.items() if tier == 2 and p != "domains harmonize"
                                         and p != "vault migrate-frontmatter"))
def test_an_unbuilt_dry_run_refuses_before_acting(path: str) -> None:
    from click.testing import CliRunner

    command = dict(_leaves())[path]
    flag = next(p for p in command.params if isinstance(p, click.Option) and "--dryRun" in p.opts)
    if flag.callback is None or flag.expose_value:
        pytest.skip("this command has a real --dryRun")
    words = path.split()
    if words[:2] == ["db", "mappings"]:
        words.insert(2, "synthetic_table")  # the table comes before the subcommand
    result = CliRunner().invoke(cli, [*words, "--dryRun"], standalone_mode=False)
    assert isinstance(result.exception, click.ClickException), result.output
    assert result.exception.message == f"--dryRun is not available for `{path}` yet; nothing was changed"


# --- --shape (spec 2026-11-05-bounded-output-design.md §4) ---------------------------


def _command(path: str) -> click.Command:
    cmd: click.Command = cli
    for part in path.split():
        cmd = cmd.commands[part]  # type: ignore[attr-defined]
    return cmd


@pytest.mark.parametrize("path", sorted(cc.SHAPE_COMMANDS))
def test_shape_commands_take_shape_and_compact(path: str) -> None:
    cmd = _command(path)
    shape = next((p for p in cmd.params if isinstance(p, click.Option) and "--shape" in p.opts), None)
    assert shape is not None, f"{path} lost --shape"
    assert isinstance(shape.type, click.Choice) and list(shape.type.choices) == ["brief", "full"]
    assert shape.default == "full"
    assert "--compact" in _long_opts(cmd)


def _brief_rows(path: str, monkeypatch) -> list[dict]:
    """Run `query <path> --shape brief` over test_brief_shape's mocked sessions; return rows with doc keys."""
    import json as _json

    from click.testing import CliRunner
    from _neo4j_recorder import Recorder
    from test_brief_shape import _patch, _survey_respond, _vt_respond
    from test_query_layer import _RecordingSession, _hub_responses, _install_session

    from artmind import graph_query as gq

    monkeypatch.delenv("ARTMIND_OUTPUT_BUDGET", raising=False)
    monkeypatch.setenv("ARTMIND_NO_PROXY", "1")
    base = ["query", *path.split()[1:], "--domain", "notes", "--shape", "brief", "--compact"]
    if path == "query survey":
        _patch(monkeypatch, Recorder(_survey_respond()))
        args, key = [*base, "termination notice"], "documents"
    elif path == "query vector-text":
        _patch(monkeypatch, Recorder(_vt_respond))
        args, key = [*base, "q"], "rows"
    elif path == "query chunks":
        row = {"chunk": {"id": "c1", "doc_id": "d1", "_domain": "notes", "text": "t"},
               "document": {"id": "d1", "name": "d1.md", "_domain": "notes", "title": "d1.md",
                            "date": "2021-02-03", "superseded": False}}
        monkeypatch.setattr(gq, "_run_read_query", lambda c, p: [_json.loads(_json.dumps(row))])
        args, key = [*base, "--idList", "c1"], "rows"
    else:
        _install_session(monkeypatch, _RecordingSession(_hub_responses()))
        args, key = [*base, "--entityId", "e1", "--includeChunks", "2"], "rows"
    r = CliRunner().invoke(cli, args)
    assert r.exit_code == 0, r.output
    rows = _json.loads(r.stdout)[key]
    if path == "query entity-context":
        rows = [c for row in rows for c in row["chunks"]]
    return rows


#: Commands whose brief rows are document/chunk rows; the graph commands' brief rows are
#: entity rows, checked in test_graph_brief.py.
_DOC_ROW_COMMANDS = frozenset({"query survey", "query vector-text", "query chunks", "query entity-context"})


@pytest.mark.parametrize("path", sorted(cc.SHAPE_COMMANDS & _DOC_ROW_COMMANDS))
def test_shape_brief_rows_carry_the_brief_keys(path: str, monkeypatch) -> None:
    rows = _brief_rows(path, monkeypatch)
    assert rows, path
    for row in rows:
        doc = row.get("doc") if "doc" in row else row  # survey rows are flat, the rest nest `doc`
        assert {"title", "date", "status"} <= set(doc), (path, sorted(doc))
        assert "doc_id" in row or "id" in row.get("doc", {}), path
