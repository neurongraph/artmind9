import base64
import json
from unittest.mock import MagicMock, patch

import pytest
from _fake_credentials import FAKE_CREDENTIAL

from artmind.extraction import call_llm, embed_text, ibm_ica_client_env
from artmind.llm_providers import describe_image_openrouter, _openrouter_client, _reset_clients
from utils.functions import resolve_llm_model


@pytest.fixture(autouse=True)
def _clear_llm_client_cache():
    _reset_clients()
    yield
    _reset_clients()


def test_call_llm_dispatches_to_ollama_by_default():
    with patch("artmind.extraction.load_env", return_value={}), patch(
        "artmind.extraction.call_llm_ollama", return_value="hi"
    ) as mock_ollama, patch("artmind.extraction.call_llm_openrouter") as mock_openrouter:
        result = call_llm("some-model", "prompt")
    assert result == "hi"
    mock_ollama.assert_called_once()
    mock_openrouter.assert_not_called()


def test_call_llm_dispatches_to_openrouter_when_configured():
    env = {"ARTMIND_KG_LLM_PROVIDER": "openrouter"}
    with patch("artmind.extraction.load_env", return_value=env), patch(
        "artmind.extraction.call_llm_openrouter", return_value="hi"
    ) as mock_openrouter, patch("artmind.extraction.call_llm_ollama") as mock_ollama:
        result = call_llm("some-model", "prompt")
    assert result == "hi"
    mock_openrouter.assert_called_once()
    mock_ollama.assert_not_called()


def test_call_llm_dispatches_to_ibm_ica_via_openrouter_client():
    env = {
        "ARTMIND_KG_LLM_PROVIDER": "ibm_ica",
        "ANTHROPIC_AUTH_TOKEN": FAKE_CREDENTIAL,
        "ANTHROPIC_BASE_URL": "https://gateway.example.com/ica/v1",
    }
    with patch("artmind.extraction.load_env", return_value=env), patch(
        "artmind.extraction.call_llm_openrouter", return_value="hi"
    ) as mock_openrouter, patch("artmind.extraction.call_llm_ollama") as mock_ollama:
        result = call_llm("claude-haiku-4-5", "prompt")
    assert result == "hi"
    mock_ollama.assert_not_called()
    mock_openrouter.assert_called_once()
    args, _ = mock_openrouter.call_args
    sent_env = args[3]
    assert sent_env["ARTMIND_OPENROUTER_API_KEY"] == FAKE_CREDENTIAL
    assert sent_env["ARTMIND_KG_LLM_URL"] == "https://gateway.example.com/ica/v1"


def test_call_llm_ibm_ica_raises_without_auth_token():
    env = {"ARTMIND_KG_LLM_PROVIDER": "ibm_ica"}
    with patch("artmind.extraction.load_env", return_value=env):
        with pytest.raises(RuntimeError, match="ANTHROPIC_AUTH_TOKEN"):
            call_llm("claude-haiku-4-5", "prompt")


def test_ibm_ica_client_env_maps_anthropic_vars_onto_openrouter_shape():
    env = {
        "ANTHROPIC_AUTH_TOKEN": FAKE_CREDENTIAL,
        "ANTHROPIC_BASE_URL": "https://gateway.example.com/ica/v1",
        "OTHER_KEY": "unchanged",
    }
    mapped = ibm_ica_client_env(env)
    assert mapped["ARTMIND_OPENROUTER_API_KEY"] == FAKE_CREDENTIAL
    assert mapped["ARTMIND_KG_LLM_URL"] == "https://gateway.example.com/ica/v1"
    assert mapped["OTHER_KEY"] == "unchanged"
    assert env.get("ARTMIND_OPENROUTER_API_KEY") is None  # original env untouched


def test_ibm_ica_client_env_raises_without_auth_token():
    with pytest.raises(RuntimeError, match="ANTHROPIC_AUTH_TOKEN"):
        ibm_ica_client_env({})


def test_ibm_ica_client_env_omits_url_when_base_url_unset():
    mapped = ibm_ica_client_env({"ANTHROPIC_AUTH_TOKEN": FAKE_CREDENTIAL})
    assert "ARTMIND_KG_LLM_URL" not in mapped


def test_ibm_ica_client_env_prefers_the_rescued_url_over_the_shadowed_one():
    """Regression: found live -- artmind-query worked, artmind-update 404'd,
    in the same chat session. ARTMIND_SDK_BASE_URL (webui/backends/
    __init__.py's _sdk_env_overrides) overrides ANTHROPIC_BASE_URL on the
    whole spawned `claude` CLI process for the CLI's own routing; a Bash
    tool call the agent makes inherits that override too, shadowing the
    value this ibm_ica client actually needs. _sdk_env_overrides rescues the
    original under ARTMIND_KG_ANTHROPIC_BASE_URL before overriding -- this
    must be preferred over the (now wrong) plain name when both are present."""
    env = {
        "ANTHROPIC_AUTH_TOKEN": FAKE_CREDENTIAL,
        # shadowed by the SDK backend's own routing override:
        "ANTHROPIC_BASE_URL": "https://gateway.example.com/ica",
        # rescued by _sdk_env_overrides before that override was applied:
        "ARTMIND_KG_ANTHROPIC_BASE_URL": "https://gateway.example.com/ica/v1",
    }
    mapped = ibm_ica_client_env(env)
    assert mapped["ARTMIND_KG_LLM_URL"] == "https://gateway.example.com/ica/v1"


def test_ibm_ica_client_env_falls_back_to_plain_url_outside_chat_ui():
    """A direct CLI invocation (no chat UI, no ARTMIND_SDK_BASE_URL override
    ever applied) never sets the rescued name -- ANTHROPIC_BASE_URL alone
    must still work exactly as before."""
    env = {
        "ANTHROPIC_AUTH_TOKEN": FAKE_CREDENTIAL,
        "ANTHROPIC_BASE_URL": "https://gateway.example.com/ica/v1",
    }
    mapped = ibm_ica_client_env(env)
    assert mapped["ARTMIND_KG_LLM_URL"] == "https://gateway.example.com/ica/v1"


def test_embed_text_raises_when_provider_is_openrouter():
    env = {"ARTMIND_KG_EMBEDDINGS_PROVIDER": "openrouter"}
    with patch("artmind.extraction.load_env", return_value=env):
        with pytest.raises(RuntimeError, match="does not provide an embeddings API"):
            embed_text("some-model", "text")


def test_embed_text_uses_ollama_by_default():
    with patch("artmind.extraction.load_env", return_value={}), patch(
        "artmind.extraction.embed_text_ollama", return_value=[0.1, 0.2]
    ) as mock_embed:
        result = embed_text("some-model", "text")
    assert result == [0.1, 0.2]
    mock_embed.assert_called_once_with("some-model", "text", host=None)


def test_embed_text_passes_embeddings_url_as_host():
    env = {"ARTMIND_KG_EMBEDDINGS_URL": "http://embed-host:11434"}
    with patch("artmind.extraction.load_env", return_value=env), patch(
        "artmind.extraction.embed_text_ollama", return_value=[0.1, 0.2]
    ) as mock_embed:
        embed_text("some-model", "text")
    mock_embed.assert_called_once_with("some-model", "text", host="http://embed-host:11434")


def test_call_llm_passes_kg_llm_url_as_ollama_host():
    env = {"ARTMIND_KG_LLM_URL": "http://llm-host:11434"}
    with patch("artmind.extraction.load_env", return_value=env), patch(
        "artmind.extraction.call_llm_ollama", return_value="hi"
    ) as mock_ollama:
        call_llm("some-model", "prompt")
    mock_ollama.assert_called_once_with(
        "some-model", "prompt", 120, host="http://llm-host:11434", max_tokens=32000,
    )


def test_call_llm_asks_for_the_output_budget_on_every_call():
    """An endpoint's own default (4096 completion tokens on one gateway, thinking
    included) is too small for an extraction answer, so the budget is explicit."""
    from artmind.extraction import DEFAULT_MAX_TOKENS

    env = {"ARTMIND_KG_LLM_PROVIDER": "openrouter"}
    with patch("artmind.extraction.load_env", return_value=env), patch(
        "artmind.extraction.call_llm_openrouter", return_value="hi"
    ) as mock_openrouter:
        call_llm("some-model", "prompt")
        call_llm("some-model", "prompt", max_tokens=9000)
    assert mock_openrouter.call_args_list[0].kwargs["max_tokens"] == DEFAULT_MAX_TOKENS
    assert mock_openrouter.call_args_list[1].kwargs["max_tokens"] == 9000


def test_the_output_budget_is_configurable_and_zero_sends_none():
    for value, sent in (("12000", 12000), ("0", None)):
        env = {"ARTMIND_KG_LLM_PROVIDER": "openrouter", "ARTMIND_KG_MAX_TOKENS": value}
        with patch("artmind.extraction.load_env", return_value=env), patch(
            "artmind.extraction.call_llm_openrouter", return_value="hi"
        ) as mock_openrouter:
            call_llm("some-model", "prompt")
        assert mock_openrouter.call_args.kwargs.get("max_tokens") == sent


def test_ollama_client_passes_host_through():
    from artmind.llm_providers import _ollama_client

    with patch("artmind.llm_providers.ollama.Client") as mock_client_cls:
        _ollama_client(30, "http://example.com:11434")
    mock_client_cls.assert_called_once_with(host="http://example.com:11434", timeout=30)


def test_ollama_client_defaults_host_to_none_when_unset():
    from artmind.llm_providers import _ollama_client

    with patch("artmind.llm_providers.ollama.Client") as mock_client_cls:
        _ollama_client(30)
    mock_client_cls.assert_called_once_with(host=None, timeout=30)


def test_embed_text_ollama_passes_host_to_client():
    from artmind.llm_providers import embed_text_ollama

    with patch("artmind.llm_providers.ollama.Client") as mock_client_cls:
        mock_client = mock_client_cls.return_value
        mock_client.embed.return_value = MagicMock(embeddings=[[0.1, 0.2]])
        result = embed_text_ollama("embed-model", "text", host="http://example.com:11434")

    mock_client_cls.assert_called_once_with(host="http://example.com:11434")
    mock_client.embed.assert_called_once_with(model="embed-model", input="text")
    assert result == [0.1, 0.2]


def test_openrouter_client_raises_without_api_key():
    from artmind.llm_providers import call_llm_openrouter

    with pytest.raises(RuntimeError, match="ARTMIND_OPENROUTER_API_KEY"):
        call_llm_openrouter("some-model", "prompt", 30, {})


def test_openrouter_client_uses_default_base_url_when_unset():
    client = _openrouter_client({"ARTMIND_OPENROUTER_API_KEY": FAKE_CREDENTIAL}, 30)
    assert str(client.base_url) == "https://openrouter.ai/api/v1/"


def test_describe_image_openrouter_sends_base64_vision_payload(tmp_path):
    image = tmp_path / "test.png"
    image.write_bytes(b"fake-image-bytes")
    env = {"ARTMIND_OPENROUTER_API_KEY": FAKE_CREDENTIAL}

    fake_response = MagicMock()
    fake_response.choices[0].message.content = "a description"

    with patch("artmind.llm_providers.openai.OpenAI") as mock_openai_cls:
        mock_client = mock_openai_cls.return_value
        mock_client.chat.completions.create.return_value = fake_response
        result = describe_image_openrouter(image, "vision-model", "describe this", 30, env)

    assert result == "a description"
    _, kwargs = mock_client.chat.completions.create.call_args
    assert kwargs["model"] == "vision-model"
    content = kwargs["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "describe this"}
    data_url = content[1]["image_url"]["url"]
    assert data_url.startswith("data:image/png;base64,")
    b64_payload = data_url.split(",", 1)[1]
    assert base64.b64decode(b64_payload) == b"fake-image-bytes"


def test_call_llm_openrouter_raises_clear_error_on_null_choices():
    from artmind.llm_providers import call_llm_openrouter

    env = {"ARTMIND_OPENROUTER_API_KEY": FAKE_CREDENTIAL}
    fake_response = MagicMock()
    fake_response.choices = None
    fake_response.error = {"message": "Provider returned error", "code": 502}

    with patch("artmind.llm_providers.openai.OpenAI") as mock_openai_cls:
        mock_client = mock_openai_cls.return_value
        mock_client.chat.completions.create.return_value = fake_response
        with pytest.raises(RuntimeError, match="OpenRouter returned no completion choices"):
            call_llm_openrouter("some-model", "prompt", 30, env)


def test_resolve_llm_model_defaults_by_provider():
    assert resolve_llm_model({}) == "ministral-3:14b"
    assert resolve_llm_model({"ARTMIND_KG_LLM_PROVIDER": "openrouter"}) == "google/gemma-4-31b-it"


def test_resolve_llm_model_respects_explicit_env_value():
    env = {"ARTMIND_KG_LLM_PROVIDER": "openrouter", "ARTMIND_KG_LLM_MODEL": "anthropic/claude-3.5-sonnet"}
    assert resolve_llm_model(env) == "anthropic/claude-3.5-sonnet"


def test_resolve_llm_model_override_wins():
    assert resolve_llm_model({}, override="custom-model") == "custom-model"


def test_call_llm_timeout_and_max_retries_override_the_defaults():
    env = {"ARTMIND_KG_LLM_PROVIDER": "openrouter", "ARTMIND_OLLAMA_TIMEOUT": "600"}
    with patch("artmind.extraction.load_env", return_value=env), patch(
        "artmind.extraction.call_llm_openrouter", return_value="hi"
    ) as mock_openrouter:
        call_llm("m", "p")
        call_llm("m", "p", timeout=30, max_retries=0)
    default_call, override_call = mock_openrouter.call_args_list
    assert default_call.args[2] == 600 and default_call.kwargs["max_retries"] is None
    assert override_call.args[2] == 30 and override_call.kwargs["max_retries"] == 0


def test_call_llm_openrouter_max_retries_is_applied_only_when_given():
    from artmind.llm_providers import call_llm_openrouter

    env = {"ARTMIND_OPENROUTER_API_KEY": FAKE_CREDENTIAL}
    with patch("artmind.llm_providers.openai.OpenAI") as mock_openai:
        client = mock_openai.return_value
        client.with_options.return_value = client
        client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content=" ok "))]
        )
        assert call_llm_openrouter("m", "p", 30, env) == "ok"
        client.with_options.assert_not_called()
        assert call_llm_openrouter("m", "p", 30, env, max_retries=0) == "ok"
        client.with_options.assert_called_once_with(max_retries=0)


# ── models that reject temperature=0 (Haiku 5.5 and the other 5.x) ───────────

LITELLM_TEMPERATURE_400 = (
    "Error code: 400 - litellm.UnsupportedParamsError: global.anthropic.claude-haiku-5-5 "
    "does not support temperature=0. Only temperature=1 is supported."
)


def _bad_request(message: str):
    import openai

    response = MagicMock(status_code=400, headers={}, request=MagicMock())
    return openai.BadRequestError(message, response=response, body=None)


def _ok(text="ok"):
    return MagicMock(choices=[MagicMock(message=MagicMock(content=text))])


def _calls_with_a_client(side_effect, *models):
    """Run call_llm_openrouter once per model against one fake client; return
    the kwargs each `create` call received, in order."""
    from artmind.llm_providers import call_llm_openrouter

    env = {"ARTMIND_OPENROUTER_API_KEY": FAKE_CREDENTIAL}
    with patch("artmind.llm_providers.openai.OpenAI") as mock_openai:
        client = mock_openai.return_value
        client.chat.completions.create.side_effect = side_effect
        for model in models:
            call_llm_openrouter(model, "p", 30, env)
        return [c.kwargs for c in client.chat.completions.create.call_args_list]


def test_a_temperature_rejection_is_retried_without_temperature():
    sent = _calls_with_a_client([_bad_request(LITELLM_TEMPERATURE_400), _ok()], "claude-haiku-5-5")
    assert [("temperature" in kw) for kw in sent] == [True, False]


def test_the_rejection_is_remembered_so_later_calls_skip_the_failing_attempt():
    sent = _calls_with_a_client(
        [_bad_request(LITELLM_TEMPERATURE_400), _ok(), _ok(), _ok()],
        "claude-haiku-5-5", "claude-haiku-5-5",
    )
    assert [("temperature" in kw) for kw in sent] == [True, False, False]


def test_the_rejection_is_per_model_not_global():
    sent = _calls_with_a_client(
        [_bad_request(LITELLM_TEMPERATURE_400), _ok(), _ok()],
        "claude-haiku-5-5", "claude-haiku-4-5",
    )
    assert sent[2]["model"] == "claude-haiku-4-5" and sent[2]["temperature"] == 0


def test_an_unrelated_bad_request_still_raises_and_is_not_retried():
    import openai

    with pytest.raises(openai.BadRequestError):
        _calls_with_a_client([_bad_request("Error code: 400 - prompt is too long")], "claude-haiku-5-5")
    # exactly one attempt: nothing was retried, nothing was learned
    from artmind.llm_providers import _no_temperature

    assert not _no_temperature


# ── the learned refusal survives the process (spec 2026-10-22 §5.2) ──────────


def _forget_in_memory_only():
    """What a new process looks like: the in-memory set is empty, the file stays."""
    from artmind import llm_providers

    llm_providers._no_temperature.clear()
    llm_providers._openrouter_clients.clear()  # a new process has no client cache either
    llm_providers._capabilities_loaded = False


def test_a_learned_refusal_is_written_to_the_capability_file():
    from artmind import llm_providers

    _calls_with_a_client([_bad_request(LITELLM_TEMPERATURE_400), _ok()], "claude-haiku-5-5")
    data = json.loads(llm_providers._capabilities_path().read_text())
    (key, entry), = data.items()
    assert key.endswith("|claude-haiku-5-5")
    assert entry["no_temperature"] is True
    assert "learned_at" in entry


def test_a_new_process_skips_the_failing_attempt():
    _calls_with_a_client([_bad_request(LITELLM_TEMPERATURE_400), _ok()], "claude-haiku-5-5")
    _forget_in_memory_only()
    sent = _calls_with_a_client([_ok()], "claude-haiku-5-5")
    assert [("temperature" in kw) for kw in sent] == [False]


def test_an_expired_entry_is_ignored():
    from artmind import llm_providers

    _calls_with_a_client([_bad_request(LITELLM_TEMPERATURE_400), _ok()], "claude-haiku-5-5")
    path = llm_providers._capabilities_path()
    data = json.loads(path.read_text())
    for entry in data.values():
        entry["learned_at"] = "2000-01-01T00:00:00+00:00"
    path.write_text(json.dumps(data))
    _forget_in_memory_only()
    sent = _calls_with_a_client([_ok()], "claude-haiku-5-5")
    assert sent[0]["temperature"] == 0


def test_a_corrupt_capability_file_is_treated_as_empty():
    from artmind import llm_providers

    path = llm_providers._capabilities_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")
    _forget_in_memory_only()
    sent = _calls_with_a_client([_ok()], "claude-haiku-5-5")
    assert sent[0]["temperature"] == 0


def test_an_unwritable_capability_file_does_not_break_the_call(monkeypatch):
    from artmind import llm_providers

    def failing_replace(src, dst):
        raise OSError("read-only logs directory")

    monkeypatch.setattr(llm_providers.os, "replace", failing_replace)
    env = {"ARTMIND_OPENROUTER_API_KEY": FAKE_CREDENTIAL}
    with patch("artmind.llm_providers.openai.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.side_effect = [
            _bad_request(LITELLM_TEMPERATURE_400),
            _ok("done"),
        ]
        assert llm_providers.call_llm_openrouter("claude-haiku-5-5", "p", 30, env) == "done"
    assert ("https://openrouter.ai/api/v1", "claude-haiku-5-5") in llm_providers._no_temperature
    logs = llm_providers._capabilities_path().parent
    assert not llm_providers._capabilities_path().exists()
    assert not list(logs.glob(".llm_capabilities.json.*.tmp"))


# ── a model that cannot be pinned says so on every run (study item 6) ────────


def test_unpinned_models_lists_a_learned_refusal_in_a_new_process():
    from artmind import llm_providers

    _calls_with_a_client([_bad_request(LITELLM_TEMPERATURE_400), _ok()], "claude-haiku-5-5")
    _forget_in_memory_only()

    assert llm_providers.temperature_not_pinned("claude-haiku-5-5", {}) is True
    assert llm_providers.temperature_not_pinned("claude-haiku-4-5", {}) is False


def test_extract_kg_warns_when_its_model_runs_without_a_pinned_temperature(monkeypatch, tmp_path):
    from loguru import logger
    from test_ingest_concurrency import _setup_extract_env

    import artmind.ingest as ing
    from artmind import llm_providers

    file_result = _setup_extract_env(monkeypatch, tmp_path, 1)
    monkeypatch.setattr(llm_providers, "temperature_not_pinned", lambda model, env: True)
    messages = []
    handle = logger.add(lambda m: messages.append(str(m)), level="WARNING")
    try:
        ing.extract_kg(file_result, "testdom", text_model="claude-haiku-5-5", max_workers=1)
    finally:
        logger.remove(handle)

    assert any("claude-haiku-5-5" in m and "temperature" in m and "reproducible" in m for m in messages)



# ── an endpoint that refuses the output budget ───────────────────────────────

ANTHROPIC_MAX_TOKENS_400 = (
    "Error code: 400 - max_tokens: 32000 > 8192, which is the maximum allowed number of "
    "output tokens for claude-haiku-4-5"
)


def _budget_calls(side_effect, *budgets, model="m"):
    from artmind.llm_providers import call_llm_openrouter

    env = {"ARTMIND_OPENROUTER_API_KEY": FAKE_CREDENTIAL}
    with patch("artmind.llm_providers.openai.OpenAI") as mock_openai:
        client = mock_openai.return_value
        client.chat.completions.create.side_effect = side_effect
        for budget in budgets:
            call_llm_openrouter(model, "p", 30, env, max_tokens=budget)
        return [c.kwargs for c in client.chat.completions.create.call_args_list]


def test_a_refused_budget_is_retried_with_the_limit_the_endpoint_names_and_remembered():
    sent = _budget_calls([_bad_request(ANTHROPIC_MAX_TOKENS_400), _ok(), _ok()], 32000, 32000)
    assert [kw.get("max_tokens") for kw in sent] == [32000, 8192, 8192]

    from artmind.llm_providers import _capabilities_path

    entry = next(iter(json.loads(_capabilities_path().read_text()).values()))
    assert entry["max_tokens_limit"] == 8192


def test_a_refusal_naming_no_limit_drops_the_parameter():
    sent = _budget_calls([_bad_request("Error code: 400 - Unsupported parameter: 'max_tokens'"), _ok()], 32000)
    assert [("max_tokens" in kw) for kw in sent] == [True, False]


def test_temperature_and_budget_refusals_are_both_learned_and_kept():
    sent = _budget_calls(
        [_bad_request(LITELLM_TEMPERATURE_400), _bad_request(ANTHROPIC_MAX_TOKENS_400), _ok()], 32000,
        model="claude-haiku-5-5",
    )
    assert [("temperature" in kw, kw.get("max_tokens")) for kw in sent] == [(True, 32000), (False, 32000), (False, 8192)]

    from artmind.llm_providers import _capabilities_path

    entry = next(iter(json.loads(_capabilities_path().read_text()).values()))
    assert entry["no_temperature"] is True and entry["max_tokens_limit"] == 8192


def test_an_unrelated_bad_request_with_a_budget_still_raises():
    import openai

    with pytest.raises(openai.BadRequestError):
        _budget_calls([_bad_request("Error code: 400 - messages: field required")], 32000)


def test_describe_image_openrouter_applies_max_retries_only_when_given(tmp_path):
    image = tmp_path / "test.png"
    image.write_bytes(b"x")
    env = {"ARTMIND_OPENROUTER_API_KEY": FAKE_CREDENTIAL}
    fake_response = MagicMock()
    fake_response.choices[0].message.content = "ok"
    with patch("artmind.llm_providers.openai.OpenAI") as mock_openai_cls:
        client = mock_openai_cls.return_value
        client.with_options.return_value.chat.completions.create.return_value = fake_response
        client.chat.completions.create.return_value = fake_response
        describe_image_openrouter(image, "m", "p", 30, env, max_retries=1)
        client.with_options.assert_called_once_with(max_retries=1)
        client.with_options.reset_mock()
        describe_image_openrouter(image, "m", "p", 30, env)
        client.with_options.assert_not_called()
