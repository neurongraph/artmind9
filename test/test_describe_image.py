"""Provider dispatch for artmind.ingest._describe_image.

No coverage existed for this dispatcher before — including for the ollama and
openrouter branches — so this file covers all three providers, not just the
ibm_ica branch added alongside these tests. See test_llm_providers.py for the
equivalent dispatch tests on the text-LLM side (call_llm).
"""

from pathlib import Path
from unittest.mock import patch

from artmind.ingest import _describe_image


IMAGE = Path("/fake/image.png")


def test_describe_image_dispatches_to_ollama_by_default():
    with patch("artmind.ingest.load_env", return_value={}), patch(
        "artmind.ingest.describe_image_ollama", return_value="a description"
    ) as mock_ollama, patch("artmind.ingest.describe_image_openrouter") as mock_openrouter:
        result = _describe_image(IMAGE, "gemma4:e4b")
    assert result == "a description"
    mock_openrouter.assert_not_called()
    mock_ollama.assert_called_once()


def test_describe_image_dispatches_to_openrouter_when_configured():
    env = {"ARTMIND_KG_LLM_PROVIDER": "openrouter"}
    with patch("artmind.ingest.load_env", return_value=env), patch(
        "artmind.ingest.describe_image_openrouter", return_value="a description"
    ) as mock_openrouter, patch("artmind.ingest.describe_image_ollama") as mock_ollama:
        result = _describe_image(IMAGE, "openai/gpt-4o-mini")
    assert result == "a description"
    mock_ollama.assert_not_called()
    mock_openrouter.assert_called_once()
    args, _ = mock_openrouter.call_args
    assert args[4] is env  # openrouter branch passes env through unmapped


def test_describe_image_dispatches_to_ibm_ica_via_openrouter_client():
    env = {
        "ARTMIND_KG_LLM_PROVIDER": "ibm_ica",
        "ANTHROPIC_AUTH_TOKEN": "enterprise-token",
        "ANTHROPIC_BASE_URL": "https://gateway.example.com/ica/v1",
    }
    with patch("artmind.ingest.load_env", return_value=env), patch(
        "artmind.ingest.describe_image_openrouter", return_value="a description"
    ) as mock_openrouter, patch("artmind.ingest.describe_image_ollama") as mock_ollama:
        result = _describe_image(IMAGE, "gpt-4o")
    assert result == "a description"
    mock_ollama.assert_not_called()
    mock_openrouter.assert_called_once()
    args, _ = mock_openrouter.call_args
    sent_env = args[4]
    assert sent_env["ARTMIND_OPENROUTER_API_KEY"] == "enterprise-token"
    assert sent_env["ARTMIND_KG_LLM_URL"] == "https://gateway.example.com/ica/v1"


def test_describe_image_returns_none_when_ibm_ica_missing_auth_token():
    # _describe_image's per-attempt try/except swallows any dispatch error
    # (missing credentials, a bad request, ...) into a None result rather than
    # propagating it — one bad image shouldn't abort the whole ingest. So the
    # missing-ANTHROPIC_AUTH_TOKEN RuntimeError from ibm_ica_client_env surfaces
    # here as a clean None + an ERROR log, not a raised exception.
    env = {"ARTMIND_KG_LLM_PROVIDER": "ibm_ica"}
    with patch("artmind.ingest.load_env", return_value=env), patch(
        "artmind.ingest.describe_image_openrouter"
    ) as mock_openrouter:
        result = _describe_image(IMAGE, "gpt-4o")
    assert result is None
    mock_openrouter.assert_not_called()


def test_describe_image_passes_kg_llm_url_as_ollama_host():
    env = {"ARTMIND_KG_LLM_URL": "http://llm-host:11434"}
    with patch("artmind.ingest.load_env", return_value=env), patch(
        "artmind.ingest.describe_image_ollama", return_value="a description"
    ) as mock_ollama:
        _describe_image(IMAGE, "gemma4:e4b")
    mock_ollama.assert_called_once()
    _, kwargs = mock_ollama.call_args
    assert kwargs["host"] == "http://llm-host:11434"
