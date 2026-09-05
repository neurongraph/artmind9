import re
from pathlib import Path

import json_repair
from loguru import logger

from artmind.llm_providers import call_llm_ollama, call_llm_openrouter, embed_text_ollama
from utils.functions import load_env, log_llm_call


def embed_text(model: str, text: str) -> list[float]:
    env = load_env()
    provider = env.get("ARTMIND_KG_EMBEDDINGS_PROVIDER", "ollama")
    if provider == "openrouter":
        raise RuntimeError(
            "OpenRouter does not provide an embeddings API; set "
            "ARTMIND_KG_EMBEDDINGS_PROVIDER=ollama (embeddings can stay on Ollama "
            "even when ARTMIND_KG_LLM_PROVIDER=openrouter)"
        )
    host = env.get("ARTMIND_KG_EMBEDDINGS_URL") or None
    embedding = embed_text_ollama(model, text, host=host)
    log_llm_call("embed", model, text, f"[embedding vector, dim={len(embedding)}]")
    return embedding


def ibm_ica_client_env(env: dict) -> dict:
    """Map ANTHROPIC_BASE_URL/ANTHROPIC_AUTH_TOKEN onto the env shape the
    OpenRouter client expects (ARTMIND_OPENROUTER_API_KEY/ARTMIND_KG_LLM_URL),
    so the ibm_ica provider reuses that same OpenAI-compatible client path
    (chat completions AND vision) instead of a separate one. Shared by
    call_llm here and by ingest.py's _describe_image.

    Checks ``ARTMIND_KG_ANTHROPIC_BASE_URL`` before the plain
    ``ANTHROPIC_BASE_URL``. The two collide whenever this command runs as a
    Bash tool call inside the chat UI with ``ARTMIND_SDK_BASE_URL`` set
    (webui/backends/__init__.py's ``_sdk_env_overrides``): that override
    replaces ``ANTHROPIC_BASE_URL`` on the *whole* spawned ``claude`` CLI
    process for the CLI's own (differently-shaped) routing, and a Bash tool
    call inherits that override too, not the value this ibm_ica client
    actually needs. ``_sdk_env_overrides`` rescues the original value under
    this name before overriding; a plain CLI invocation (no chat UI in the
    picture) never sets it, so ``ANTHROPIC_BASE_URL`` is exactly what's read
    there, unchanged.
    """
    token = env.get("ANTHROPIC_AUTH_TOKEN")
    if not token:
        raise RuntimeError(
            "ARTMIND_KG_LLM_PROVIDER=ibm_ica requires ANTHROPIC_AUTH_TOKEN to be set"
        )
    env2 = dict(env)
    env2["ARTMIND_OPENROUTER_API_KEY"] = token
    base = env.get("ARTMIND_KG_ANTHROPIC_BASE_URL") or env.get("ANTHROPIC_BASE_URL")
    if base:
        env2["ARTMIND_KG_LLM_URL"] = base
    return env2


def call_llm(model: str, prompt: str) -> str:
    env = load_env()
    timeout = int(env.get("ARTMIND_OLLAMA_TIMEOUT", "120"))
    provider = env.get("ARTMIND_KG_LLM_PROVIDER", "ollama")
    if provider == "openrouter":
        result = call_llm_openrouter(model, prompt, timeout, env)
    elif provider == "ibm_ica":
        result = call_llm_openrouter(model, prompt, timeout, ibm_ica_client_env(env))
    else:
        host = env.get("ARTMIND_KG_LLM_URL") or None
        result = call_llm_ollama(model, prompt, timeout, host=host)
    log_llm_call("chat", model, prompt, result)
    return result


def parse_json_response(text: str):
    text = text.strip()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    return json_repair.loads(text.strip())


def extract_with_retry(
    step_name: str,
    model: str,
    prompt: str,
    debug_dir: Path | None = None,
) -> tuple[list, bool]:
    # TODO(429-backoff): this retries once, immediately, on ANY exception. That was
    # fine while ingestion issued LLM calls one-at-a-time. Now that extract_kg fans
    # chunks out concurrently (ARTMIND_INGEST_MAX_WORKERS / --maxWorkers), several
    # requests can hit a rate-limited provider (e.g. OpenRouter) at once and come
    # back HTTP 429 "Too Many Requests". An immediate retry likely just earns
    # another 429 and then marks the chunk failed — not a real extraction failure,
    # just backpressure. If 429s show up in practice, special-case them here:
    # detect the 429 (vs a genuine error), and retry with exponential backoff +
    # jitter (e.g. 1s, 2s, 4s) for a few attempts before giving up. Honour a
    # Retry-After header if the provider sends one. Keep the current fast single
    # retry for non-rate-limit errors so real failures still fail fast.
    raw_llm = ""
    for attempt in range(2):
        try:
            raw_llm = call_llm(model, prompt)
            return parse_json_response(raw_llm), True
        except Exception as e:
            if attempt == 0:
                logger.warning("  {} failed (attempt 1/2), retrying: {}", step_name, e)
            else:
                logger.error("  {} failed after 2 attempts: {}", step_name, e)
                if raw_llm and debug_dir:
                    safe = re.sub(r"[^A-Za-z0-9_]", "_", step_name)
                    (debug_dir / f"debug_{safe}.txt").write_text(raw_llm, encoding="utf-8")
    return [], False


def entities_list_text(entities: list[dict]) -> str:
    return "\n".join(f"{e['id']} ({e['entity_class']}): {e['name']}" for e in entities)


def build_entities_prompt(text: str, schema: dict, vocabulary: list | None = None) -> str:
    """`vocabulary` is the retrieved name vocabulary (see
    `artmind.canonicalize.retrieve_vocabulary`) — omitted, the prompt renders
    exactly as before."""
    from artmind.prompt_builder import assemble_entities_prompt

    return assemble_entities_prompt(schema, vocabulary=vocabulary).replace("{text}", text)


def build_properties_prompt(
    text: str, entities: list[dict], schema: dict, vocabulary: dict | None = None
) -> str:
    """`vocabulary` is the retrieved property-key vocabulary (see
    `artmind.canonicalize.retrieve_property_vocabulary`) — omitted, the
    prompt renders exactly as before."""
    from artmind.prompt_builder import assemble_properties_prompt

    ent_list = entities_list_text(entities)
    return (
        assemble_properties_prompt(schema, vocabulary=vocabulary)
        .replace("{entities_list}", ent_list)
        .replace("{text}", text)
    )


def build_relationships_prompt(text: str, entities: list[dict], schema: dict) -> str:
    from artmind.prompt_builder import assemble_relationships_prompt

    ent_list = entities_list_text(entities)
    return (
        assemble_relationships_prompt(schema)
        .replace("{entities_list}", ent_list)
        .replace("{text}", text)
    )
