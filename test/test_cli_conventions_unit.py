"""Unit tests for artmind/cli_conventions.py (spec 2026-11-02-cli-uniformity-design.md)."""

import click
import pytest
from click.testing import CliRunner

from artmind import cli_conventions as cc


def _run(cmd, args):
    return CliRunner().invoke(click.Group("root", commands={"cmd": cmd}), ["cmd", *args])


# ── envelope ────────────────────────────────────────────────────────────────

def test_envelope_puts_keys_first_and_keeps_the_rest_in_order() -> None:
    out = cc.envelope({"zeta": 1, "alpha": 2, "next": "do x"}, None)
    assert list(out) == ["command", "warnings", "next", "zeta", "alpha"]
    assert out["warnings"] == [] and out["next"] == "do x"


def test_envelope_defaults_and_wraps() -> None:
    assert cc.envelope({}, None) == {"command": None, "warnings": [], "next": None}
    assert cc.envelope({"warnings": "careful"}, None)["warnings"] == ["careful"]
    assert cc.envelope({"warnings": ["a", "b"]}, None)["warnings"] == ["a", "b"]


def test_envelope_replaces_command_with_the_click_path() -> None:
    seen = {}

    @click.command("leaf")
    @click.pass_context
    def leaf(ctx):
        seen["out"] = cc.envelope({"command": "old_name", "x": 1}, ctx)

    sub = click.Group("grp", commands={"leaf": leaf})
    root = click.Group("root", commands={"grp": sub})
    CliRunner().invoke(root, ["grp", "leaf"])
    assert seen["out"]["command"] == "grp leaf"
    assert seen["out"]["x"] == 1


def test_envelope_passes_non_dicts_through() -> None:
    assert cc.envelope([1, 2], None) == [1, 2]


# ── aliases ─────────────────────────────────────────────────────────────────

def _aliased_cmd():
    @click.command()
    @cc.dry_run_option()
    @cc.aliased_option("--stageOnly", "--stage-only", param="stage_only", is_flag=True)
    @cc.aliased_option("--minScore", "--min-score", param="min_score", type=float, default=0.5)
    @cc.compact_option
    def cmd(dry_run, stage_only, min_score, compact):
        click.echo(f"{dry_run}|{stage_only}|{min_score}|{compact}")

    return cmd


def test_new_spelling_parses_without_a_note() -> None:
    r = _run(_aliased_cmd(), ["--dryRun", "--stageOnly", "--minScore", "0.9", "--compact"])
    assert r.exit_code == 0, r.output
    assert r.stdout.strip() == "True|True|0.9|True"
    assert "artmind:" not in r.stderr


def test_old_spelling_parses_to_the_same_value_and_notes_once_on_stderr() -> None:
    r = _run(_aliased_cmd(), ["--dry-run", "--min-score=0.9"])
    assert r.exit_code == 0, r.output
    assert r.stdout.strip() == "True|False|0.9|False"
    notes = [l for l in r.stderr.splitlines() if l.startswith("artmind:")]
    assert len(notes) == 2
    assert "--dry-run is now --dryRun" in notes[0]
    assert "--min-score is now --minScore" in notes[1]


def test_old_spellings_are_hidden_from_help() -> None:
    r = _run(_aliased_cmd(), ["--help"])
    assert "--dryRun" in r.stdout and "--stageOnly" in r.stdout
    assert "--dry-run" not in r.stdout and "--stage-only" not in r.stdout


def test_unbuilt_dry_run_refuses_before_acting() -> None:
    acted = []

    @click.command()
    @cc.unbuilt_dry_run_option
    def cmd():
        acted.append(1)

    r = _run(cmd, ["--dryRun"])
    assert r.exit_code == cc.EXIT_INPUT and not acted
    assert "not available" in r.output
    assert _run(cmd, []).exit_code == 0 and acted


# ── effect ──────────────────────────────────────────────────────────────────

def test_effect_to_json_shape_and_sample_cap() -> None:
    eff = cc.Effect(
        summary="Merge 3 entities.",
        changes=[cc.Change("entity_merge", 30, [{"i": n} for n in range(30)])],
        stale_topics=["acme"], reversible=True, undo="edit x", records=[".artmind/x.yaml"],
    )
    out = eff.to_json()
    assert list(out) == ["summary", "changes", "stale", "llm", "reversible", "undo", "travels", "records"]
    assert out["changes"][0]["count"] == 30 and len(out["changes"][0]["sample"]) == cc.SAMPLE_CAP
    assert out["stale"] == {"topics": ["acme"], "views": []}
    assert out["llm"] == {"calls": 0, "model": None, "est_cost_usd": None}


def test_unknown_change_kind_is_rejected() -> None:
    with pytest.raises(ValueError):
        cc.Change("entity_explode", 1)


def test_constants() -> None:
    assert (cc.EXIT_OK, cc.EXIT_INPUT, cc.EXIT_USAGE, cc.EXIT_STORE) == (0, 1, 2, 3)
    assert cc.SERVER_COMMANDS == {"serve", "chat-ui", "admin-ui"}
    assert cc.WRITE_COMMANDS["sameas approve"] == 1 and cc.WRITE_COMMANDS["db grain"] == 2
    assert sum(1 for t in cc.WRITE_COMMANDS.values() if t == 1) == 24


# ── _echo_json applies the envelope ─────────────────────────────────────────

def test_echo_json_adds_the_envelope_from_the_click_path() -> None:
    import json

    from artmind.cli import _echo_json

    @click.command("leaf")
    @cc.compact_option
    def leaf(compact):
        _echo_json({"command": "old_name", "rows": [1], "warnings": "w"}, compact)

    root = click.Group("root", commands={"grp": click.Group("grp", commands={"leaf": leaf})})
    r = CliRunner().invoke(root, ["grp", "leaf", "--compact"])
    assert r.exit_code == 0, r.output
    out = json.loads(r.stdout)
    assert list(out) == ["command", "warnings", "next", "rows"]
    assert out == {"command": "grp leaf", "warnings": ["w"], "next": None, "rows": [1]}
    assert r.stdout.strip() == json.dumps(out, separators=(",", ":"), ensure_ascii=False)


def test_echo_json_merges_domain_guard_warnings_then_envelope() -> None:
    import json

    from artmind.cli import _UNKNOWN_DOMAIN_META, _echo_json

    @click.command("leaf")
    @click.pass_context
    def leaf(ctx):
        ctx.find_root().meta[_UNKNOWN_DOMAIN_META] = ["unknown domain x"]
        _echo_json({"warnings": ["a"]})

    r = CliRunner().invoke(click.Group("root", commands={"leaf": leaf}), ["leaf"])
    assert json.loads(r.stdout)["warnings"] == ["a", "unknown domain x"]
