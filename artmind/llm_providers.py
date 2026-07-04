import base64
import mimetypes
from pathlib import Path

import ollama
import openai

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def _openrouter_client(env: dict, timeout: int) -> openai.OpenAI:
    api_key = env.get("ARTMIND_OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ARTMIND_KG_LLM_PROVIDER=openrouter requires ARTMIND_OPENROUTER_API_KEY to be set"
        )
    base_url = env.get("ARTMIND_KG_LLM_URL") or OPENROUTER_BASE_URL
    return openai.OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)


def _image_data_url(image: Path) -> str:
    mime = mimetypes.guess_type(image.name)[0] or "application/octet-stream"
    b64 = base64.b64encode(image.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _first_choice_content(response) -> str:
    if not response.choices:
        err = getattr(response, "error", None)
        raise RuntimeError(f"OpenRouter returned no completion choices: {err or response}")
    return (response.choices[0].message.content or "").strip()


def call_llm_ollama(model: str, prompt: str, timeout: int) -> str:
    response = ollama.Client(timeout=timeout).chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        options={"temperature": 0},
    )
    return (response.message.content or "").strip()


def call_llm_openrouter(model: str, prompt: str, timeout: int, env: dict) -> str:
    client = _openrouter_client(env, timeout)
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )
    return _first_choice_content(response)


def embed_text_ollama(model: str, text: str) -> list[float]:
    response = ollama.embed(model=model, input=text)
    return response.embeddings[0]


def describe_image_ollama(image: Path, model: str, prompt: str, timeout: int) -> str:
    response = ollama.Client(timeout=timeout).chat(
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
