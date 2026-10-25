import re
from pathlib import Path

import json_repair
from loguru import logger

from artmind.llm_providers import TruncatedText, call_llm_ollama, call_llm_openrouter, embed_text_ollama
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


_SHADOW_HINTED = False


def ibm_ica_shadow_hint(env: dict) -> str | None:
    """A hint when the ibm_ica endpoint is shadowed, else None.

    artmind loads its config files without overriding real environment
    variables, so an ``ANTHROPIC_BASE_URL`` some other tool exported -- Claude
    Code does, for its own endpoint -- beats the one in config.env, and ibm_ica
    requests go there (typically a 404). ``ARTMIND_KG_ANTHROPIC_BASE_URL`` is
    checked first (``ibm_ica_client_env``), so setting it in config.env fixes
    it. The hint names the file, never an endpoint value.
    """
    if env.get("ARTMIND_KG_ANTHROPIC_BASE_URL"):
        return None
    live = (env.get("ANTHROPIC_BASE_URL") or "").strip()
    if not live:
        return None
    import paths
    from dotenv import dotenv_values

    for config in paths.LOADED_ENV_FILES:
        configured = ((dotenv_values(config) or {}).get("ANTHROPIC_BASE_URL") or "").strip()
        if configured and configured != live:
            return (
                f"ANTHROPIC_BASE_URL from the process environment overrides the one in {config} "
                "(environment variables win over config files), so ibm_ica requests went to the "
                "environment's endpoint. If another tool exported it (Claude Code does), set "
                f"ARTMIND_KG_ANTHROPIC_BASE_URL in {config} to the ibm_ica endpoint -- it is checked first."
            )
    return None


def _warn_ibm_ica_shadow(env: dict, exc: Exception) -> None:
    """Log the shadowing hint once per process, on a 404 from ibm_ica."""
    global _SHADOW_HINTED
    if _SHADOW_HINTED or "404" not in str(exc):
        return
    try:
        hint = ibm_ica_shadow_hint(env)
    except Exception:
        return
    if hint:
        _SHADOW_HINTED = True
        logger.warning(hint)


def call_llm(
    model: str, prompt: str, call_type: str = "chat",
    timeout: int | None = None, max_retries: int | None = None,
    max_tokens: int | None = None,
) -> str:
    """`timeout` (seconds) overrides ARTMIND_OLLAMA_TIMEOUT for this call;
    `max_retries` overrides the openrouter/ibm_ica SDK's own retry count;
    `max_tokens` caps the answer (None = the provider's default). An answer
    the provider cut off at its limit comes back as a `TruncatedText`."""
    env = load_env()
    if timeout is None:
        timeout = int(env.get("ARTMIND_OLLAMA_TIMEOUT", "120"))
    provider = env.get("ARTMIND_KG_LLM_PROVIDER", "ollama")
    cap = {"max_tokens": max_tokens} if max_tokens else {}
    if provider == "openrouter":
        result = call_llm_openrouter(model, prompt, timeout, env, max_retries=max_retries, **cap)
    elif provider == "ibm_ica":
        try:
            result = call_llm_openrouter(
                model, prompt, timeout, ibm_ica_client_env(env), max_retries=max_retries, **cap,
            )
        except Exception as exc:
            _warn_ibm_ica_shadow(env, exc)
            raise
    else:
        host = env.get("ARTMIND_KG_LLM_URL") or None
        result = call_llm_ollama(model, prompt, timeout, host=host, **cap)
    log_llm_call(call_type, model, prompt, result)
    return result


def _strip_wrapping(text: str) -> str:
    text = text.strip()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()


def parse_json_response(text: str):
    return json_repair.loads(_strip_wrapping(text))


#: The output-token cap of the one retry a cut-off answer gets. Override with
#: ARTMIND_KG_TRUNCATION_MAX_TOKENS when an endpoint refuses this much.
TRUNCATION_RETRY_MAX_TOKENS = 32000


class TruncatedAnswer(RuntimeError):
    """An LLM answer was still cut off after the retry with more output room.
    `extract_with_retry(raise_on_truncation=True)` raises it so the caller can
    split its input and ask again in smaller pieces."""


def _json_start(body: str) -> int | None:
    """Where the answer's JSON begins: the body itself when it opens with `[`
    or `{`, else the first line that does (prose before the JSON may hold a
    bracket of its own -- "see [below]:"), else the first bracket anywhere."""
    if body[:1] in ("[", "{"):
        return 0
    line = re.search(r"(?m)^[ \t]*[\[{]", body)
    if line:
        return line.end() - 1
    return next((i for i, ch in enumerate(body) if ch in "[{"), None)


def _closes(body: str, single_quotes: bool) -> bool:
    """Does the bracket opened at body[0] close? Strings are skipped: `"..."`,
    and with `single_quotes` also a `'...'` that opens where a JSON value or
    key could (after `[`, `{`, `,` or `:`), the Python-literal style
    json_repair also accepts."""
    depth, quote, escaped, last = 0, "", False, ""
    for ch in body:
        if quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = ""
                last = ch
            continue
        if ch == '"' or (single_quotes and ch == "'" and last in "[{,:"):
            quote = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
            if depth <= 0:
                return True
        if not ch.isspace():
            last = ch
    return False


def is_cut_off(text: str) -> bool:
    """Was this answer cut off? Either the provider said so (`TruncatedText`,
    a "length" stop) or its JSON opens and never closes. `json_repair` would
    otherwise close it silently and hand back a half-written last item (an
    entity with no `entity_class`). Sloppy JSON that does close (a trailing
    comma, prose after the closing bracket, single-quoted strings) and prose
    without JSON are not truncation."""
    if isinstance(text, TruncatedText):
        return True
    body = _strip_wrapping(str(text))
    start = _json_start(body)
    if start is None:
        return False
    body = body[start:]
    return not (_closes(body, single_quotes=False) or _closes(body, single_quotes=True))


def _truncation_retry_max_tokens() -> int:
    try:
        return int(load_env().get("ARTMIND_KG_TRUNCATION_MAX_TOKENS") or TRUNCATION_RETRY_MAX_TOKENS)
    except ValueError:
        return TRUNCATION_RETRY_MAX_TOKENS


def extract_with_retry(
    step_name: str,
    model: str,
    prompt: str,
    debug_dir: Path | None = None,
    *,
    raise_on_truncation: bool = False,
) -> tuple[list, bool]:
    """`(items, ok)`. Two attempts. An answer that was cut off (`is_cut_off`)
    is never repaired into a result: the second attempt asks again with
    `TRUNCATION_RETRY_MAX_TOKENS` of output room, and if that is cut off too
    the step fails -- or, with `raise_on_truncation`, raises `TruncatedAnswer`
    so the caller can split its input."""
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
    cut_off = False  # sticky: any attempt that was cut off makes the failure a truncation
    for attempt in range(2):
        try:
            # Only pass max_tokens when raising it: callers (and test doubles)
            # that predate it keep the two-argument call.
            raw_llm = (
                call_llm(model, prompt, max_tokens=_truncation_retry_max_tokens())
                if cut_off else call_llm(model, prompt)
            )
            if is_cut_off(raw_llm):
                cut_off = True
                if attempt == 0:
                    logger.warning(
                        "  {} answer was cut off (attempt 1/2), retrying with max_tokens={}",
                        step_name, _truncation_retry_max_tokens(),
                    )
                else:
                    logger.error("  {} answer was still cut off after the retry", step_name)
                continue
            return parse_json_response(raw_llm), True
        except Exception as e:
            if attempt == 0:
                logger.warning("  {} failed (attempt 1/2), retrying: {}", step_name, e)
            else:
                logger.error("  {} failed after 2 attempts: {}", step_name, e)
    if raw_llm and debug_dir:
        safe = re.sub(r"[^A-Za-z0-9_]", "_", step_name)
        try:
            (debug_dir / f"debug_{safe}.txt").write_text(raw_llm, encoding="utf-8")
        except OSError as e:  # a debug copy is never a reason to fail the step differently
            logger.debug("  could not save the raw {} answer: {}", step_name, e)
    if cut_off and raise_on_truncation:
        raise TruncatedAnswer(f"{step_name}: the answer was cut off at the output-token limit")
    return [], False


def entities_list_text(entities: list[dict]) -> str:
    """One line per entity for the properties/relationships prompts. Tolerates
    an incomplete entity (the extraction drops those first, but a caller that
    did not must not crash on one -- a `KeyError` here once aborted files)."""
    return "\n".join(
        f"{e.get('id', '?')} ({e.get('entity_class') or '?'}): {e.get('name', '')}" for e in entities
    )


def build_entities_prompt(text: str, schema: dict, vocabulary: list | None = None, *, meta: dict | None = None) -> str:
    """`vocabulary` is the retrieved name vocabulary (see
    `artmind.canonicalize.retrieve_vocabulary`) — omitted, the prompt renders
    exactly as before. `meta`: an alternative meta.yaml dict (a proposed
    prompt template, for `artmind review reextract`); None = the run folder's."""
    from artmind.prompt_builder import assemble_entities_prompt

    return assemble_entities_prompt(schema, meta=meta, vocabulary=vocabulary).replace("{text}", text)


def build_properties_prompt(
    text: str,
    entities: list[dict],
    schema: dict,
    vocabulary: dict | None = None,
    anchor_date: str | None = None,
    *,
    meta: dict | None = None,
) -> str:
    """`vocabulary` is the retrieved property-key vocabulary (see
    `artmind.canonicalize.retrieve_property_vocabulary`); `anchor_date` the
    document's own date, for resolving relative phrases (see
    `prompt_builder._render_anchor_date_section`) — either omitted, the
    prompt renders exactly as before. `meta`: an alternative meta.yaml dict (a
    proposed prompt template, for `artmind review reextract`); None = the run
    folder's."""
    from artmind.prompt_builder import assemble_properties_prompt

    ent_list = entities_list_text(entities)
    return (
        assemble_properties_prompt(schema, meta=meta, vocabulary=vocabulary, anchor_date=anchor_date)
        .replace("{entities_list}", ent_list)
        .replace("{text}", text)
    )


def build_relationships_prompt(text: str, entities: list[dict], schema: dict, *, meta: dict | None = None) -> str:
    """`meta`: an alternative meta.yaml dict (a proposed prompt template, for
    `artmind review reextract`); None = the run folder's."""
    from artmind.prompt_builder import assemble_relationships_prompt

    ent_list = entities_list_text(entities)
    return (
        assemble_relationships_prompt(schema, meta=meta)
        .replace("{entities_list}", ent_list)
        .replace("{text}", text)
    )
