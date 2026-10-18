"""A hint when another tool's ANTHROPIC_BASE_URL shadows the ibm_ica endpoint."""

import pytest
from _fake_credentials import FAKE_CREDENTIAL

from artmind import extraction

# conftest replaces extraction.call_llm in every test (the suite is hermetic);
# keep the real function -- its network layer, call_llm_openrouter, is stubbed below.
_REAL_CALL_LLM = extraction.call_llm


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    import paths

    f = tmp_path / "config.env"
    f.write_text("ANTHROPIC_BASE_URL=https://ica.example/v1\n")
    monkeypatch.setattr(paths, "LOADED_ENV_FILES", [f])
    monkeypatch.setattr(extraction, "_SHADOW_HINTED", False)
    return f


def test_hint_when_environment_differs_from_config(config_file):
    hint = extraction.ibm_ica_shadow_hint({"ANTHROPIC_BASE_URL": "https://other-tool.example"})
    assert hint is not None
    assert str(config_file) in hint and "ARTMIND_KG_ANTHROPIC_BASE_URL" in hint
    assert "other-tool.example" not in hint and "ica.example" not in hint   # no endpoint values in logs


@pytest.mark.parametrize("env", [
    {"ANTHROPIC_BASE_URL": "https://ica.example/v1"},                                   # same as config
    {"ANTHROPIC_BASE_URL": "https://other.example", "ARTMIND_KG_ANTHROPIC_BASE_URL": "x"},  # rescue already set
    {},                                                                                 # nothing set
])
def test_no_hint(config_file, env):
    assert extraction.ibm_ica_shadow_hint(env) is None


def test_no_hint_when_config_sets_no_endpoint(tmp_path, monkeypatch):
    import paths

    f = tmp_path / "config.env"
    f.write_text("ARTMIND_KG_LLM_PROVIDER=ibm_ica\n")
    monkeypatch.setattr(paths, "LOADED_ENV_FILES", [f])
    assert extraction.ibm_ica_shadow_hint({"ANTHROPIC_BASE_URL": "https://other.example"}) is None


def _ibm_ica_env():
    return {"ARTMIND_KG_LLM_PROVIDER": "ibm_ica", "ANTHROPIC_AUTH_TOKEN": FAKE_CREDENTIAL,
            "ANTHROPIC_BASE_URL": "https://other-tool.example"}


def test_call_llm_logs_hint_once_on_404_and_reraises(config_file, monkeypatch):
    warnings = []
    monkeypatch.setattr(extraction, "load_env", _ibm_ica_env)
    monkeypatch.setattr(extraction.logger, "warning", lambda msg, *a: warnings.append(msg))

    def not_found(*a, **k):
        raise RuntimeError("404 page not found")

    monkeypatch.setattr(extraction, "call_llm_openrouter", not_found)
    for _ in range(2):
        with pytest.raises(RuntimeError, match="404"):
            _REAL_CALL_LLM("m", "p")
    assert len(warnings) == 1 and "ARTMIND_KG_ANTHROPIC_BASE_URL" in warnings[0]


def test_call_llm_no_hint_for_other_errors(config_file, monkeypatch):
    warnings = []
    monkeypatch.setattr(extraction, "load_env", _ibm_ica_env)
    monkeypatch.setattr(extraction.logger, "warning", lambda msg, *a: warnings.append(msg))

    def timeout(*a, **k):
        raise TimeoutError("read timed out")

    monkeypatch.setattr(extraction, "call_llm_openrouter", timeout)
    with pytest.raises(TimeoutError):
        _REAL_CALL_LLM("m", "p")
    assert warnings == []
