import base64
import mimetypes
from pathlib import Path

import ollama
import openai
from loguru import logger

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Client SDKs (ollama.Client, openai.OpenAI) each open their own HTTP connection
# pool on construction. Building a new one per call leaks file descriptors until
# the process hits its fd limit, so callers below are cached and reused per
# (api_key, base_url, timeout) / timeout combination.
_openrouter_clients: dict[tuple[str, str, int], openai.OpenAI] = {}
_ollama_clients: dict[tuple[str | None, int], ollama.Client] = {}
_ollama_embed_clients: dict[str | None, ollama.Client] = {}

# (base_url, model) pairs whose endpoint rejected `temperature`. Newer Claude
# models (Haiku 5.5 and the other 5.x) accept only the default sampling values
# and answer a `temperature=0` request with a 400, so after the first rejection
# calls to that pair simply leave it out.
_no_temperature: set[tuple[str, str]] = set()


def _reset_clients() -> None:
    """Drop cached SDK clients and learned model limits. Intended for test isolation only."""
    _openrouter_clients.clear()
    _no_temperature.clear()
    _ollama_clients.clear()
    _ollama_embed_clients.clear()


def _openrouter_client(env: dict, timeout: int) -> openai.OpenAI:
    api_key = env.get("ARTMIND_OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ARTMIND_KG_LLM_PROVIDER=openrouter requires ARTMIND_OPENROUTER_API_KEY to be set"
        )
    base_url = env.get("ARTMIND_KG_LLM_URL") or OPENROUTER_BASE_URL
    key = (api_key, base_url, timeout)
    client = _openrouter_clients.get(key)
    if client is None:
        client = openai.OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        _openrouter_clients[key] = client
    return client


def _ollama_client(timeout: int, host: str | None = None) -> ollama.Client:
    key = (host, timeout)
    client = _ollama_clients.get(key)
    if client is None:
        client = ollama.Client(host=host, timeout=timeout)
        _ollama_clients[key] = client
    return client


def _ollama_embed_client(host: str | None = None) -> ollama.Client:
    # Separate cache from _ollama_client: embed calls carry no timeout (the
    # SDK's own default applies), so they're keyed on host alone.
    client = _ollama_embed_clients.get(host)
    if client is None:
        client = ollama.Client(host=host)
        _ollama_embed_clients[host] = client
    return client


def _image_data_url(image: Path) -> str:
    mime = mimetypes.guess_type(image.name)[0] or "application/octet-stream"
    b64 = base64.b64encode(image.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _first_choice_content(response) -> str:
    if not response.choices:
        err = getattr(response, "error", None)
        raise RuntimeError(f"OpenRouter returned no completion choices: {err or response}")
    return (response.choices[0].message.content or "").strip()


def call_llm_ollama(model: str, prompt: str, timeout: int, host: str | None = None) -> str:
    response = _ollama_client(timeout, host).chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        options={"temperature": 0},
    )
    return (response.message.content or "").strip()


def call_llm_openrouter(
    model: str, prompt: str, timeout: int, env: dict, max_retries: int | None = None,
) -> str:
    """`max_retries` overrides the SDK's own (default 2, which also retries a
    timeout -- so one hung request can cost 3x `timeout` before it raises)."""
    client = _openrouter_client(env, timeout)
    if max_retries is not None:
        client = client.with_options(max_retries=max_retries)
    kind = (env.get("ARTMIND_KG_LLM_URL") or OPENROUTER_BASE_URL, model)
    request = {"model": model, "messages": [{"role": "user", "content": prompt}]}
    if kind not in _no_temperature:
        request["temperature"] = 0  # reproducible extraction where the model allows it
    try:
        response = client.chat.completions.create(**request)
    except openai.BadRequestError as exc:
        if "temperature" not in request or not _rejects_temperature(exc):
            raise
        _no_temperature.add(kind)
        logger.warning(
            "{} does not accept temperature=0 ({}); calling it without a temperature "
            "from now on, so its output is no longer pinned to be reproducible",
            model, exc.message,
        )
        del request["temperature"]
        response = client.chat.completions.create(**request)
    return _first_choice_content(response)


def _rejects_temperature(exc: Exception) -> bool:
    """Is this 400 the endpoint saying the model won't take our `temperature`?

    Matched on the message, because gateways word it differently (litellm:
    "does not support temperature=0. Only temperature=1 is supported"; the
    Anthropic API: "`temperature` may not be set ...") and a 400 about anything
    else must still raise."""
    text = str(exc).lower()
    return "temperature" in text and any(
        marker in text for marker in ("not support", "unsupported", "only temperature", "may not be set", "not allowed")
    )


def embed_text_ollama(model: str, text: str, host: str | None = None) -> list[float]:
    response = _ollama_embed_client(host).embed(model=model, input=text)
    return response.embeddings[0]


def describe_image_ollama(
    image: Path, model: str, prompt: str, timeout: int, host: str | None = None
) -> str:
    response = _ollama_client(timeout, host).chat(
        model=model,
        messages=[{"role": "user", "content": prompt, "images": [str(image)]}],
    )
    return (response.message.content or "").strip()


def describe_image_openrouter(
    image: Path, model: str, prompt: str, timeout: int, env: dict
) -> str:
    client = _openrouter_client(env, timeout)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": _image_data_url(image)}},
                ],
            }
        ],
    )
    return _first_choice_content(response)
