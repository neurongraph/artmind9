"""The agent contract (P3 spec §8.1, D5): a profile picks the classic or the bundle query skill."""

import pytest

from artmind.webui import profiles


def test_profiles_default_to_classic():
    for p in profiles.PROFILES.values():
        assert p.contract == "classic"
        assert "artmind-query" in p.skills and "artmind-query-bundle" not in p.skills


@pytest.mark.parametrize("base", [profiles.ADMIN_PROFILE, profiles.BENCHMARK_PROFILE, profiles.QA_PROFILE])
def test_with_contract_bundle_swaps_the_query_skill_in_place(base):
    p = profiles.with_contract(base, "bundle")
    assert p.contract == "bundle"
    assert "artmind-query-bundle" in p.skills and "artmind-query" not in p.skills
    assert p.skills.index("artmind-query-bundle") == base.skills.index("artmind-query")  # same position
    assert set(p.skills) - {"artmind-query-bundle"} == set(base.skills) - {"artmind-query"}
    assert "artmind-query-bundle" in p.system_append and "query run" in p.system_append
    assert (p.name, p.acp_mode, p.filesystem_access) == (base.name, base.acp_mode, base.filesystem_access)


def test_with_contract_classic_is_identity():
    assert profiles.with_contract(profiles.ADMIN_PROFILE, "classic") is profiles.ADMIN_PROFILE


def test_an_unknown_contract_is_refused():
    with pytest.raises(ValueError, match="classic, bundle"):
        profiles.with_contract(profiles.ADMIN_PROFILE, "fast")


@pytest.mark.parametrize("raw, expected", [(None, "classic"), ("", "classic"), ("classic", "classic"),
                                           ("bundle", "bundle"), (" Bundle ", "bundle")])
def test_contract_from_env(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("ARTMIND_AGENT_CONTRACT", raising=False)
    else:
        monkeypatch.setenv("ARTMIND_AGENT_CONTRACT", raw)
    assert profiles.contract_from_env() == expected


def test_a_bad_env_contract_is_refused(monkeypatch):
    monkeypatch.setenv("ARTMIND_AGENT_CONTRACT", "fast")
    with pytest.raises(ValueError, match="ARTMIND_AGENT_CONTRACT"):
        profiles.contract_from_env()


def test_require_skill_checks_the_bundle_skill_is_installed(tmp_path, monkeypatch):
    import paths

    monkeypatch.setattr(paths, "ARTMIND_AGENT_CWD", tmp_path)
    bundle = profiles.with_contract(profiles.ADMIN_PROFILE, "bundle")
    with pytest.raises(ValueError, match="artmind init"):
        profiles.require_skill(bundle)
    skill = tmp_path / ".claude" / "skills" / "artmind-query-bundle"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("invented\n")
    profiles.require_skill(bundle)  # now fine
    profiles.require_skill(profiles.ADMIN_PROFILE)  # classic never checked
