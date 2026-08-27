import base64
from unittest.mock import MagicMock, patch

import pytest

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
        "ANTHROPIC_AUTH_TOKEN": "enterprise-token",
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
    assert sent_env["ARTMIND_OPENROUTER_API_KEY"] == "enterprise-token"
    assert sent_env["ARTMIND_KG_LLM_URL"] == "https://gateway.example.com/ica/v1"


def test_call_llm_ibm_ica_raises_without_auth_token():
    env = {"ARTMIND_KG_LLM_PROVIDER": "ibm_ica"}
    with patch("artmind.extraction.load_env", return_value=env):
        with pytest.raises(RuntimeError, match="ANTHROPIC_AUTH_TOKEN"):
            call_llm("claude-haiku-4-5", "prompt")


def test_ibm_ica_client_env_maps_anthropic_vars_onto_openrouter_shape():
    env = {
        "ANTHROPIC_AUTH_TOKEN": "enterprise-token",
        "ANTHROPIC_BASE_URL": "https://gateway.example.com/ica/v1",
        "OTHER_KEY": "unchanged",
    }
    mapped = ibm_ica_client_env(env)
    assert mapped["ARTMIND_OPENROUTER_API_KEY"] == "enterprise-token"
    assert mapped["ARTMIND_KG_LLM_URL"] == "https://gateway.example.com/ica/v1"
    assert mapped["OTHER_KEY"] == "unchanged"
    assert env.get("ARTMIND_OPENROUTER_API_KEY") is None  # original env untouched


def test_ibm_ica_client_env_raises_without_auth_token():
    with pytest.raises(RuntimeError, match="ANTHROPIC_AUTH_TOKEN"):
        ibm_ica_client_env({})


def test_ibm_ica_client_env_omits_url_when_base_url_unset():
    mapped = ibm_ica_client_env({"ANTHROPIC_AUTH_TOKEN": "tok"})
    assert "ARTMIND_KG_LLM_URL" not in mapped


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
    mock_ollama.assert_called_once_with("some-model", "prompt", 120, host="http://llm-host:11434")


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
    client = _openrouter_client({"ARTMIND_OPENROUTER_API_KEY": "key"}, 30)
    assert str(client.base_url) == "https://openrouter.ai/api/v1/"


def test_describe_image_openrouter_sends_base64_vision_payload(tmp_path):
    image = tmp_path / "test.png"
    image.write_bytes(b"fake-image-bytes")
    env = {"ARTMIND_OPENROUTER_API_KEY": "key"}

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

    env = {"ARTMIND_OPENROUTER_API_KEY": "key"}
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
