"""Skill variants (spec 2026-11-08 §2.2): `classic` is artmind-query; variant X is artmind-query-X."""

import pytest

from artmind.webui import profiles
from artmind.webui.profiles import ADMIN_PROFILE, BENCHMARK_PROFILE, skill_for, variant_from_env, with_variant


def test_classic_is_the_plain_skill():
    assert skill_for("classic") == "artmind-query"
    assert skill_for("p2-1") == "artmind-query-p2-1"


@pytest.mark.parametrize("bad", ["", "Bad", "a b", "../x", "-x", "x" * 41])
def test_variant_names_are_checked(bad):
    with pytest.raises(ValueError):
        skill_for(bad)


def test_with_variant_swaps_the_query_skill_and_records_the_variant():
    p = with_variant(BENCHMARK_PROFILE, "p2-1")
    assert "artmind-query-p2-1" in p.skills and "artmind-query" not in p.skills
    assert p.contract == "p2-1"
    assert "artmind-query-p2-1" in p.system_append


def test_with_variant_classic_is_identity():
    assert with_variant(ADMIN_PROFILE, "classic") is ADMIN_PROFILE


def test_variant_from_env(monkeypatch):
    monkeypatch.delenv(profiles.CONTRACT_ENV, raising=False)
    assert variant_from_env() == "classic"
    monkeypatch.setenv(profiles.CONTRACT_ENV, "p2-1")
    assert variant_from_env() == "p2-1"
    monkeypatch.setenv(profiles.CONTRACT_ENV, "Not Valid")
    with pytest.raises(ValueError):
        variant_from_env()


def test_require_skill_checks_the_variant_folder(tmp_path, monkeypatch):
    monkeypatch.setattr("paths.ARTMIND_AGENT_CWD", tmp_path)
    profiles.require_skill(with_variant(BENCHMARK_PROFILE, "classic"))  # classic never checked
    with pytest.raises(ValueError, match="artmind-query-p2-1"):
        profiles.require_skill(with_variant(BENCHMARK_PROFILE, "p2-1"))
    skill = tmp_path / ".claude" / "skills" / "artmind-query-p2-1"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("x")
    profiles.require_skill(with_variant(BENCHMARK_PROFILE, "p2-1"))
