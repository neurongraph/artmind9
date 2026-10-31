"""Provider dispatch for artmind.ingest._describe_image.

No coverage existed for this dispatcher before — including for the ollama and
openrouter branches — so this file covers all three providers, not just the
ibm_ica branch added alongside these tests. See test_llm_providers.py for the
equivalent dispatch tests on the text-LLM side (call_llm).
"""

from pathlib import Path
from unittest.mock import patch
from _fake_credentials import FAKE_CREDENTIAL

from artmind.ingest import _describe_image


IMAGE = Path("/fake/image.png")


def test_describe_image_dispatches_to_ollama_by_default():
    with patch("artmind.ingest.load_env", return_value={}), patch(
        "artmind.extraction.describe_image_ollama", return_value="a description"
    ) as mock_ollama, patch("artmind.extraction.describe_image_openrouter") as mock_openrouter:
        result = _describe_image(IMAGE, "gemma4:e4b")
    assert result == "a description"
    mock_openrouter.assert_not_called()
    mock_ollama.assert_called_once()


def test_describe_image_dispatches_to_openrouter_when_configured():
    env = {"ARTMIND_KG_LLM_PROVIDER": "openrouter"}
    with patch("artmind.ingest.load_env", return_value=env), patch(
        "artmind.extraction.describe_image_openrouter", return_value="a description"
    ) as mock_openrouter, patch("artmind.extraction.describe_image_ollama") as mock_ollama:
        result = _describe_image(IMAGE, "openai/gpt-4o-mini")
    assert result == "a description"
    mock_ollama.assert_not_called()
    mock_openrouter.assert_called_once()
    args, _ = mock_openrouter.call_args
    assert args[4] is env  # openrouter branch passes env through unmapped


def test_describe_image_dispatches_to_ibm_ica_via_openrouter_client():
    env = {
        "ARTMIND_KG_LLM_PROVIDER": "ibm_ica",
        "ANTHROPIC_AUTH_TOKEN": FAKE_CREDENTIAL,
        "ANTHROPIC_BASE_URL": "https://gateway.example.com/ica/v1",
    }
    with patch("artmind.ingest.load_env", return_value=env), patch(
        "artmind.extraction.describe_image_openrouter", return_value="a description"
    ) as mock_openrouter, patch("artmind.extraction.describe_image_ollama") as mock_ollama:
        result = _describe_image(IMAGE, "gpt-4o")
    assert result == "a description"
    mock_ollama.assert_not_called()
    mock_openrouter.assert_called_once()
    args, _ = mock_openrouter.call_args
    sent_env = args[4]
    assert sent_env["ARTMIND_OPENROUTER_API_KEY"] == FAKE_CREDENTIAL
    assert sent_env["ARTMIND_KG_LLM_URL"] == "https://gateway.example.com/ica/v1"


def test_describe_image_returns_none_when_ibm_ica_missing_auth_token():
    # _describe_image's per-attempt try/except swallows any dispatch error
    # (missing credentials, a bad request, ...) into a None result rather than
    # propagating it — one bad image shouldn't abort the whole ingest. So the
    # missing-ANTHROPIC_AUTH_TOKEN RuntimeError from ibm_ica_client_env surfaces
    # here as a clean None + an ERROR log, not a raised exception.
    env = {"ARTMIND_KG_LLM_PROVIDER": "ibm_ica"}
    with patch("artmind.ingest.load_env", return_value=env), patch(
        "artmind.extraction.describe_image_openrouter"
    ) as mock_openrouter:
        result = _describe_image(IMAGE, "gpt-4o")
    assert result is None
    mock_openrouter.assert_not_called()


def test_describe_image_passes_kg_llm_url_as_ollama_host():
    env = {"ARTMIND_KG_LLM_URL": "http://llm-host:11434"}
    with patch("artmind.ingest.load_env", return_value=env), patch(
        "artmind.extraction.describe_image_ollama", return_value="a description"
    ) as mock_ollama:
        _describe_image(IMAGE, "gemma4:e4b")
    mock_ollama.assert_called_once()
    _, kwargs = mock_ollama.call_args
    assert kwargs["host"] == "http://llm-host:11434"


def _timeout_passed(env):
    with patch("artmind.ingest.load_env", return_value=env), patch(
        "artmind.extraction.describe_image_openrouter", return_value="a description"
    ) as mock_openrouter:
        _describe_image(IMAGE, "m")
    return mock_openrouter.call_args


def test_image_timeout_prefers_its_own_setting():
    env = {"ARTMIND_KG_LLM_PROVIDER": "openrouter", "ARTMIND_IMAGE_TIMEOUT": "90", "ARTMIND_OLLAMA_TIMEOUT": "600"}
    args, _ = _timeout_passed(env)
    assert args[3] == 90


def test_image_timeout_falls_back_to_the_llm_timeout_then_120():
    args, _ = _timeout_passed({"ARTMIND_KG_LLM_PROVIDER": "openrouter", "ARTMIND_OLLAMA_TIMEOUT": "600"})
    assert args[3] == 600
    args, _ = _timeout_passed({"ARTMIND_KG_LLM_PROVIDER": "openrouter"})
    assert args[3] == 120


def test_image_timeout_ignores_an_empty_setting():
    args, _ = _timeout_passed({"ARTMIND_KG_LLM_PROVIDER": "openrouter", "ARTMIND_IMAGE_TIMEOUT": "", "ARTMIND_OLLAMA_TIMEOUT": "300"})
    assert args[3] == 300


def test_image_max_retries_reaches_the_client_for_openrouter_and_ibm_ica():
    env = {"ARTMIND_KG_LLM_PROVIDER": "openrouter", "ARTMIND_IMAGE_MAX_RETRIES": "1"}
    _, kwargs = _timeout_passed(env)
    assert kwargs["max_retries"] == 1
    env = {
        "ARTMIND_KG_LLM_PROVIDER": "ibm_ica", "ARTMIND_IMAGE_MAX_RETRIES": "0",
        "ANTHROPIC_AUTH_TOKEN": FAKE_CREDENTIAL, "ANTHROPIC_BASE_URL": "https://gateway.example.com/v1",
    }
    _, kwargs = _timeout_passed(env)
    assert kwargs["max_retries"] == 0


def test_image_max_retries_unset_leaves_the_sdk_default():
    _, kwargs = _timeout_passed({"ARTMIND_KG_LLM_PROVIDER": "openrouter"})
    assert kwargs["max_retries"] is None


# ── _describe_images: several images in flight, results in image order ──────────

import threading
import time

import pytest

from artmind import ingest as ing


@pytest.fixture
def images(tmp_path):
    paths = []
    for n in range(6):
        p = tmp_path / f"image_{n:06d}.png"
        p.write_bytes(b"x")
        paths.append(p)
    return paths


def _workers(monkeypatch, value):
    env = {} if value is None else {"ARTMIND_IMAGE_WORKERS": value}
    monkeypatch.setattr(ing, "load_env", lambda *a, **k: env)


def test_image_workers_setting():
    assert ing._image_workers({}, 5) == 1
    assert ing._image_workers({"ARTMIND_IMAGE_WORKERS": "4"}, 5) == 4
    assert ing._image_workers({"ARTMIND_IMAGE_WORKERS": "4"}, 2) == 2
    assert ing._image_workers({"ARTMIND_IMAGE_WORKERS": "0"}, 5) == 1
    assert ing._image_workers({"ARTMIND_IMAGE_WORKERS": " "}, 5) == 1
    assert ing._image_workers({"ARTMIND_IMAGE_WORKERS": "many"}, 5) == 1
    assert ing._image_workers({"ARTMIND_IMAGE_WORKERS": "4"}, 0) == 1


def test_descriptions_map_to_their_own_image_whatever_order_calls_finish(images, monkeypatch):
    _workers(monkeypatch, "4")

    def describe(image, model):
        # the first images take longest, so they finish last
        time.sleep(0.05 * (len(images) - int(image.stem[-6:])) / len(images))
        return f"desc of {image.name}"

    monkeypatch.setattr(ing, "_describe_image", describe)
    result = ing._describe_images(images, "m")
    assert list(result) == images
    assert all(result[i] == f"desc of {i.name}" for i in images)
    for i in images:
        assert (i.with_name(i.name + "_desc.md")).read_text() == f"desc of {i.name}"


def test_calls_really_overlap_up_to_the_worker_count(images, monkeypatch):
    _workers(monkeypatch, "3")
    lock = threading.Lock()
    state = {"now": 0, "peak": 0}

    def describe(image, model):
        with lock:
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
        time.sleep(0.05)
        with lock:
            state["now"] -= 1
        return "d"

    monkeypatch.setattr(ing, "_describe_image", describe)
    ing._describe_images(images, "m")
    assert state["peak"] == 3


def test_default_is_sequential(images, monkeypatch):
    _workers(monkeypatch, None)
    order = []
    monkeypatch.setattr(ing, "_describe_image", lambda image, model: order.append(image) or "d")
    ing._describe_images(images, "m")
    assert order == images


def test_one_failing_image_leaves_the_others_intact(images, monkeypatch):
    _workers(monkeypatch, "4")

    def describe(image, model):
        if image == images[2]:
            raise RuntimeError("boom")
        if image == images[3]:
            return None
        return f"desc {image.name}"

    monkeypatch.setattr(ing, "_describe_image", describe)
    result = ing._describe_images(images, "m")
    assert result[images[2]] is None and result[images[3]] is None
    assert [result[i] for i in (images[0], images[1], images[4], images[5])] == [
        f"desc {i.name}" for i in (images[0], images[1], images[4], images[5])
    ]
    assert not images[2].with_name(images[2].name + "_desc.md").exists()


def test_stored_descriptions_skip_the_model_only_when_reuse_is_on(images, monkeypatch):
    _workers(monkeypatch, "4")
    for i in images[:3]:
        i.with_name(i.name + "_desc.md").write_text("stored")
    images[3].with_name(images[3].name + "_desc.md").write_text("  \n")  # blank: not reusable
    called = []
    monkeypatch.setattr(ing, "_describe_image", lambda image, model: called.append(image) or "fresh")

    result = ing._describe_images(images, "m", reuse_stored=True)
    assert sorted(called) == sorted(images[3:])
    assert [result[i] for i in images] == ["stored"] * 3 + ["fresh"] * 3

    called.clear()
    ing._describe_images(images, "m", reuse_stored=False)
    assert sorted(called) == sorted(images)
