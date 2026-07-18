"""The agent-profile seam: one persona per web-UI surface, threaded through
both backends. Guards that admin/QA scoping stays de-hardcoded."""

import pytest

from artmind.webui.agent import agent_options
from artmind.webui.backends import backend_factory, create_backend
from artmind.webui.profiles import ADMIN_PROFILE, PROFILES, QA_PROFILE


@pytest.fixture(autouse=True)
def _clean_acp_env(monkeypatch):
    """create_backend('acp', ...) reads env knobs; isolate the tests from them."""
    for var in (
        "ARTMIND_ACP_MODE",
        "ARTMIND_ACP_CWD",
        "ARTMIND_ACP_AGENT_CMD",
        "ARTMIND_ACP_PROMPT_PREAMBLE",
    ):
        monkeypatch.delenv(var, raising=False)


def test_profile_registry_and_acp_modes():
    assert PROFILES == {"qa": QA_PROFILE, "admin": ADMIN_PROFILE}
    assert QA_PROFILE.acp_mode == "artmind"
    assert ADMIN_PROFILE.acp_mode == "artmind-admin"


def test_qa_is_read_and_contribute_only():
    # End users may ask (query) and contribute/correct facts (update) — nothing
    # that maintains the graph, authors schemas, or ingests documents.
    assert set(QA_PROFILE.skills) == {"artmind-query", "artmind-update"}
    for operator_skill in (
        "artmind-refine",
        "artmind-create-schema",
        "artmind-ingestion-helper",
    ):
        assert operator_skill not in QA_PROFILE.skills


def test_admin_owns_the_full_maintenance_set():
    # Everything QA can do, plus the operator-only skills — ingestion firmly
    # here (guidance/troubleshooting), never in QA.
    assert set(QA_PROFILE.skills) <= set(ADMIN_PROFILE.skills)
    for operator_skill in (
        "artmind-refine",
        "artmind-create-schema",
        "artmind-ingestion-helper",
    ):
        assert operator_skill in ADMIN_PROFILE.skills


def test_agent_options_reflects_profile():
    qa = agent_options()  # default profile
    assert qa.skills == list(QA_PROFILE.skills)
    assert qa.system_prompt["append"] == QA_PROFILE.system_append

    admin = agent_options(ADMIN_PROFILE)
    assert admin.skills == list(ADMIN_PROFILE.skills)
    assert admin.system_prompt["append"] == ADMIN_PROFILE.system_append
    assert admin.skills != qa.skills


def test_acp_backend_wears_profile():
    qa = create_backend("acp")  # default
    assert qa._mode == "artmind"
    assert qa._preamble_text == QA_PROFILE.system_append

    admin = create_backend("acp", ADMIN_PROFILE)
    assert admin._mode == "artmind-admin"
    assert admin._preamble_text == ADMIN_PROFILE.system_append


def test_acp_mode_env_still_overrides_profile(monkeypatch):
    monkeypatch.setenv("ARTMIND_ACP_MODE", "custom-mode")
    backend = create_backend("acp", ADMIN_PROFILE)
    assert backend._mode == "custom-mode"


def test_backend_factory_binds_profile():
    factory = backend_factory(ADMIN_PROFILE)
    backend = factory("acp")  # SessionRegistry calls the factory with a name
    assert backend._mode == "artmind-admin"
    assert backend._preamble_text == ADMIN_PROFILE.system_append


def test_unknown_backend_rejected():
    with pytest.raises(ValueError):
        create_backend("nonsense")
