from datetime import datetime
from loguru import logger
import os
import re
import shlex
import subprocess
import threading
import time
from pathlib import Path
from dotenv import load_dotenv, dotenv_values
from paths import (
    LLM_CALLS_LOG_FILE,
)

# ── .env loader ─────────────────────────────────────────────────────
def load_env(path: Path | None = None) -> dict[str, str | None]:
    """The effective configuration, merged across every file `paths` loaded.

    `paths.py` loads the vault's `config.env`, then a legacy `.env`, then the
    machine-wide `config.env`, each with ``override=False`` -- so ``os.environ``
    already IS the merged view, with real environment variables beating all of
    them. Returning it keeps the ~30 ``env.get("ARTMIND_...")`` call sites
    correct under the machine/vault config split (docs/vault.md): a vault
    `config.env` that sensibly holds only the graph connection must not hide the
    machine's model settings, which used to happen silently -- callers fell back
    to hardcoded defaults while ``os.environ`` held the right value.

    Pass ``path`` to read one specific file instead, for a caller that genuinely
    means a single file rather than the effective configuration.
    """
    if path is not None:
        values = dotenv_values(path)
        if values:
            load_dotenv(path)
        return values or {}
    return dict(os.environ)


def resolve_llm_model(env: dict, override: str | None = None) -> str:
    """Resolve the chat/text LLM model, defaulting per ARTMIND_KG_LLM_PROVIDER."""
    if override:
        return override
    provider = env.get("ARTMIND_KG_LLM_PROVIDER", "ollama")
    default = "google/gemma-4-31b-it" if provider == "openrouter" else "ministral-3:14b"
    return env.get("ARTMIND_KG_LLM_MODEL", default)



_SEP = "=" * 72
# Serializes appends to the shared LLM audit log. Concurrent ingest workers
# (chunk-level fan-out) each log their call here; without the lock, large
# prompt/response entries can interleave in the file. Held only for the write,
# never around the LLM request itself, so it doesn't throttle concurrency.
_LLM_LOG_LOCK = threading.Lock()


def log_llm_call(call_type: str, model: str, prompt: str, response: str) -> None:
    LLM_CALLS_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().isoformat(timespec="milliseconds")
    entry = (
        f"\n{_SEP}\n"
        f"# {ts}  |  TYPE: {call_type}  |  MODEL: {model}\n"
        f"{_SEP}\n"
        f"# === PROMPT ===\n"
        f"{prompt}\n"
        f"# === RESPONSE ===\n"
        f"{response}\n"
    )
    with _LLM_LOG_LOCK:
        with open(LLM_CALLS_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(entry)


_ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

def run_command(
    cmd_str: str,
    timeout: int | None = None,
    cwd: Path | None = None,
    extra_env: dict | None = None,
) -> tuple[int, str, str]:
    logger.debug("CMD: {}", cmd_str)
    if timeout is not None:
        logger.debug("CMD timeout: {}s", timeout)
    cmd = shlex.split(cmd_str)
    env = {**os.environ, "NO_COLOR": "1", "TERM": "dumb"}
    if extra_env:
        env.update(extra_env)
    t0 = time.monotonic()
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd, env=env)
    elapsed = time.monotonic() - t0
    if result.returncode == 0:
        logger.debug("CMD ok in {:.1f}s", elapsed)
    else:
        logger.error("CMD failed ({}) in {:.1f}s: {}", result.returncode, elapsed, result.stderr or result.stdout)
    stdout = _ANSI_ESCAPE.sub("", result.stdout)
    stderr = _ANSI_ESCAPE.sub("", result.stderr)
    return result.returncode, stdout, stderr