from loguru import logger
import os
import re
import shlex
import subprocess
import time
from pathlib import Path
from dotenv import load_dotenv, dotenv_values
from paths import (
    ENV_FILE,
 
)

# ── .env loader ─────────────────────────────────────────────────────
def load_env(path: Path = ENV_FILE) -> dict[str, str | None]:
    """Load .env file and return the parsed values as a dict.

    Also populates ``os.environ`` so that ``os.getenv()`` works
    transparently throughout the rest of the application.
    """
    values = dotenv_values(path)
    if values:
        load_dotenv(path)
    return values or {}



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