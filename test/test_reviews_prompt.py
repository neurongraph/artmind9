"""`artmind review prompt`: the ready-to-paste coding-session handoff for a dossier."""

from pathlib import Path

import pytest
from click.testing import CliRunner

from artmind.cli import cli
from artmind.reviews import dossier as dz
from artmind.reviews.handoff import coding_prompt

RID = "rv-2031-01-01-aaaaaa"
REG = (f"### R-{RID} — trips\n\n**Question:** How many trips did Mira take?\n\n"
       "**Evaluation comment:** Three, answered from the graph.")


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    import paths

    monkeypatch.setattr(paths, "REVIEWS_DIR", tmp_path / "reviews")
    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(paths, "ARTMIND_VAULT_DIR", tmp_path / "vault")


def _complete(**sections):
    secs = {t: f"filled {t}" for t in dz.SECTIONS}
    secs["Regression question"] = REG
    secs["Agent prompt"] = dz.PRIVACY_PREAMBLE + "\n\nDo the thing."
    secs.update(sections)
    d = dz.Dossier(meta={"review_id": RID, "status": "open", "stage": "schema", "elements": ["j/PERSON-WENT_TO->"],
                         "trace_ids": ["t1"], "private_terms": ["Mira"]}, sections=secs)
    dz.save(d)
    return d


def test_prompt_names_the_dossier_the_commands_and_the_rules(tmp_path):
    text = coding_prompt(_complete())
    assert f"export ARTMIND_VAULT={tmp_path / 'vault'}" in text
    assert f"artmind review show {RID} --markdown" in text
    assert f"artmind review leakcheck {RID} --base master --repo ." in text
    assert '"Agent prompt" section' in text and "new branch from master" in text
    assert "do not merge" in text.lower() and "Do not edit anything in the vault" in text
    assert "Mira" not in text and "Do the thing" not in text     # never the dossier's own content


def test_prompt_base_branch_and_no_vault(monkeypatch):
    import paths

    monkeypatch.setattr(paths, "ARTMIND_VAULT_DIR", None)
    text = coding_prompt(_complete(), base="develop")
    assert "new branch from develop" in text and "--base develop" in text
    assert "export ARTMIND_VAULT=<path to the vault>" in text


def _run(*args):
    return CliRunner().invoke(cli, ["review", "prompt", *args])


def test_cli_prints_the_prompt():
    _complete()
    result = _run(RID)
    assert result.exit_code == 0, result.output
    assert result.stdout.startswith("export ARTMIND_VAULT=")
    assert f"artmind review show {RID} --markdown" in result.stdout


def test_cli_refuses_an_incomplete_dossier_unless_forced():
    _complete(Diagnosis="<!-- todo -->")
    result = _run(RID)
    assert result.exit_code != 0
    assert "review validate" in result.output and "Diagnosis" in result.output
    assert _run(RID, "--force").exit_code == 0


def test_cli_unknown_dossier():
    result = _run("rv-2031-01-09-ffffff")
    assert result.exit_code != 0 and "no dossier" in result.output
