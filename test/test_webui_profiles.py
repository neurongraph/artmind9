"""The agent-profile seam: one persona per web-UI surface, threaded through
both backends. Guards that admin/QA scoping stays de-hardcoded."""

import pytest

from artmind.webui.agent import agent_options
from artmind.webui.backends import (
    backend_factory,
    create_backend,
    set_sdk_base_url,
    set_sdk_model,
)
from artmind.webui.profiles import ADMIN_PROFILE, BENCHMARK_PROFILE, PROFILES, QA_PROFILE


@pytest.fixture(autouse=True)
def _clean_acp_env(monkeypatch):
    """create_backend(...) reads env knobs; isolate the tests from them."""
    for var in (
        "ARTMIND_ACP_MODE",
        "ARTMIND_ACP_CWD",
        "ARTMIND_ACP_AGENT_CMD",
        "ARTMIND_ACP_PROMPT_PREAMBLE",
        "ARTMIND_SDK_MODEL",
        "ARTMIND_SDK_FALLBACK_MODEL",
        "ARTMIND_SDK_BASE_URL",
    ):
        monkeypatch.delenv(var, raising=False)
    set_sdk_model(None)  # reset the CLI-flag overrides between tests
    set_sdk_base_url(None)
    yield
    set_sdk_model(None)
    set_sdk_base_url(None)


def test_profile_registry_and_acp_modes():
    assert PROFILES == {
        "qa": QA_PROFILE, "admin": ADMIN_PROFILE, "benchmark": BENCHMARK_PROFILE,
    }
    assert QA_PROFILE.acp_mode == "artmind"
    assert ADMIN_PROFILE.acp_mode == "artmind-admin"
    assert BENCHMARK_PROFILE.acp_mode == "artmind"


def test_benchmark_is_query_only():
    # Batch benchmark runs must never write to the graph.
    assert set(BENCHMARK_PROFILE.skills) == {"artmind-query"}


def test_qa_is_read_and_contribute_only():
    # End users may ask (query) and contribute/correct facts (update) — nothing
    # that maintains the graph, authors schemas, or ingests documents.
    assert set(QA_PROFILE.skills) == {"artmind-query", "artmind-update"}
    for operator_skill in (
        "artmind-curate",
        "artmind-create-schema",
        "artmind-ingestion-helper",
    ):
        assert operator_skill not in QA_PROFILE.skills


def test_admin_owns_the_full_maintenance_set():
    # Everything QA can do, plus the operator-only skills — ingestion firmly
    # here (guidance/troubleshooting), never in QA.
    assert set(QA_PROFILE.skills) <= set(ADMIN_PROFILE.skills)
    for operator_skill in (
        "artmind-curate",
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


def test_sdk_backend_model_defaults_to_none():
    backend = create_backend("claude-sdk")
    assert backend._model is None
    assert backend._fallback_model is None


def test_sdk_backend_reads_model_env_vars(monkeypatch):
    monkeypatch.setenv("ARTMIND_SDK_MODEL", "claude-haiku-4-5")
    monkeypatch.setenv("ARTMIND_SDK_FALLBACK_MODEL", "claude-sonnet-5")
    backend = create_backend("claude-sdk")
    assert backend._model == "claude-haiku-4-5"
    assert backend._fallback_model == "claude-sonnet-5"


def test_sdk_model_cli_flag_overrides_env(monkeypatch):
    monkeypatch.setenv("ARTMIND_SDK_MODEL", "claude-haiku-4-5")
    set_sdk_model("claude-opus-5")
    backend = create_backend("claude-sdk")
    assert backend._model == "claude-opus-5"


def test_sdk_backend_env_override_absent_by_default():
    # No ARTMIND_SDK_BASE_URL: nothing should be injected, so a normal
    # operator's own ~/.claude login is left completely untouched.
    backend = create_backend("claude-sdk")
    assert backend._env is None


def test_sdk_backend_base_url_isolates_claude_config_dir(monkeypatch):
    monkeypatch.setenv("ARTMIND_SDK_BASE_URL", "https://gateway.example.com/ica")
    backend = create_backend("claude-sdk")
    assert backend._env["ANTHROPIC_BASE_URL"] == "https://gateway.example.com/ica"
    # Isolated CLAUDE_CONFIG_DIR so a personal claude.ai/console OAuth login
    # can't silently override ANTHROPIC_AUTH_TOKEN for this custom endpoint.
    assert backend._env["CLAUDE_CONFIG_DIR"].endswith(".claude-sdk-auth")


def test_sdk_base_url_cli_flag_overrides_env(monkeypatch):
    monkeypatch.setenv("ARTMIND_SDK_BASE_URL", "https://gateway.example.com/ica")
    set_sdk_base_url("https://other-gateway.example.com")
    backend = create_backend("claude-sdk")
    assert backend._env["ANTHROPIC_BASE_URL"] == "https://other-gateway.example.com"


def test_sdk_base_url_cli_flag_empty_string_forces_default(monkeypatch):
    # --base-url "" must beat a set env var and force the normal (OAuth/login)
    # routing — the documented "flip back without editing .env" escape hatch.
    monkeypatch.setenv("ARTMIND_SDK_BASE_URL", "https://gateway.example.com/ica")
    set_sdk_base_url("")
    backend = create_backend("claude-sdk")
    assert backend._env is None
