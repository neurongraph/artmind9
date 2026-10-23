"""Plan classifiers: lexical rules always; a decision model when configured (spec 2026-10-22 §4.4)."""

from __future__ import annotations

import ipaddress
import math
import os
import threading
import time
from urllib.parse import urlparse

from loguru import logger

from artmind.query_plan.classify.base import Answer, Classifier, ClassifierUnavailable, Decision
from artmind.query_plan.classify.lexical import LexicalClassifier

__all__ = ["Answer", "Classifier", "ClassifierUnavailable", "Decision", "LexicalClassifier", "select_classifier",
           "warm_up"]

DEFAULT_TIMEOUT_S = 5.0
PROVIDERS = ("ollama", "openrouter")
# OpenRouter passes the Jev decisions API through (same body, same answers), keyed by
# ARTMIND_OPENROUTER_API_KEY. The URL is fixed: ARTMIND_DECISION_URL is the ollama provider's.
OPENROUTER_DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
# OpenRouter provider preferences sent with every call: no provider that logs prompts or keeps
# them. With none left that qualifies OpenRouter answers 404 and the plan falls back to lexical.
OPENROUTER_PRIVACY = {"data_collection": "deny", "zdr": True}
# A cold load of a local model takes 40-100 s; the warm-up waits that long in its own thread.
WARMUP_TIMEOUT_S = 300.0


def _timeout(env) -> float:
    try:
        value = float(env.get("ARTMIND_DECISION_TIMEOUT") or DEFAULT_TIMEOUT_S)
    except ValueError:
        return DEFAULT_TIMEOUT_S
    return value if math.isfinite(value) and value > 0 else DEFAULT_TIMEOUT_S


def _is_loopback(host: str | None) -> bool:
    if not host:
        return False
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _check_url(url: str, api_key: str | None) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("ARTMIND_DECISION_URL must be an http:// or https:// URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("ARTMIND_DECISION_URL must not carry credentials; use ARTMIND_DECISION_API_KEY")
    if api_key and parsed.scheme == "http" and not _is_loopback(parsed.hostname):
        logger.warning("ARTMIND_DECISION_API_KEY is sent in clear text to {}: use https:// for a non-local endpoint",
                       parsed.hostname)


def _provider(env) -> str:
    provider = (env.get("ARTMIND_DECISION_PROVIDER") or "ollama").strip().casefold()
    if provider not in PROVIDERS:
        raise ValueError(f"ARTMIND_DECISION_PROVIDER must be one of {', '.join(PROVIDERS)}, not {provider!r}")
    return provider


def _endpoint(env, model_override: str | None) -> dict | None:
    """The configured decision endpoint as JevClassifier kwargs, or None when there is none.
    Raises ValueError for a half or unsafe configuration."""
    provider = _provider(env)
    model = (model_override or env.get("ARTMIND_DECISION_MODEL") or "").strip()
    if provider == "openrouter":
        if not model:
            raise ValueError("ARTMIND_DECISION_PROVIDER=openrouter needs ARTMIND_DECISION_MODEL "
                             "(e.g. typesafe/jev-1.14)")
        api_key = (env.get("ARTMIND_OPENROUTER_API_KEY") or "").strip()
        if not api_key:
            raise ValueError("ARTMIND_DECISION_PROVIDER=openrouter needs ARTMIND_OPENROUTER_API_KEY")
        return {"provider": provider, "url": OPENROUTER_DECISIONS_URL, "model": model, "api_key": api_key,
                "extra_body": {"provider": dict(OPENROUTER_PRIVACY)}}
    url = (env.get("ARTMIND_DECISION_URL") or "").strip()
    if not url:
        return None
    if not model:
        raise ValueError("ARTMIND_DECISION_URL is set but ARTMIND_DECISION_MODEL is not")
    api_key = env.get("ARTMIND_DECISION_API_KEY") or None
    _check_url(url, api_key)
    return {"provider": provider, "url": url, "model": model, "api_key": api_key, "extra_body": None}


def select_classifier(option: str, domain_choice: str, model_override: str | None,
                      env=None, timeout: float | None = None) -> tuple[Classifier, str | None]:
    """(classifier, why it is lexical) for `--classifier` and the domain config.

    `timeout` overrides ARTMIND_DECISION_TIMEOUT (shadow plans, which nobody waits on, allow longer).

    Settings are artmind-specific names (ARTMIND_DECISION_PROVIDER / _URL / _MODEL / _API_KEY /
    _TIMEOUT, ARTMIND_OPENROUTER_API_KEY), so no variable another tool exports can redirect
    them (CLAUDE.md §5). ARTMIND_DECISION_PROVIDER=openrouter ignores ARTMIND_DECISION_URL and
    ARTMIND_DECISION_API_KEY.
    """
    if option not in ("auto", "lexical", "decision"):
        raise ValueError(f"unknown classifier option {option!r}; expected auto, lexical or decision")
    env = os.environ if env is None else env
    if option == "lexical":
        return LexicalClassifier(), None
    if option == "auto" and domain_choice == "lexical":
        return LexicalClassifier(), "domain_config"
    endpoint = _endpoint(env, model_override)
    if endpoint is None:
        if option == "decision":
            raise ValueError("--classifier decision needs ARTMIND_DECISION_URL and ARTMIND_DECISION_MODEL "
                             "(or ARTMIND_DECISION_PROVIDER=openrouter)")
        return LexicalClassifier(), "no_endpoint"
    from artmind.query_plan.classify.jev import JevClassifier

    return JevClassifier(endpoint["url"], endpoint["model"], api_key=endpoint["api_key"],
                         timeout=timeout or _timeout(env), extra_body=endpoint["extra_body"]), None


def warm_up(env=None) -> threading.Thread | None:
    """Load the decision model in the background, so the first plan after start-up does not
    pay its cold load. Called by `serve`, `chat-ui` and `admin-ui` at start. One tiny request
    outside the breaker, in a daemon thread; never raises, never delays start-up.

    It only loads the model: a decision model reuses work only for a prompt it has seen
    exactly, so it cannot speed up a new question. ARTMIND_DECISION_WARMUP=0 turns it off.
    The model is ARTMIND_DECISION_MODEL (a domain config's `decision_model` is not warmed).
    ARTMIND_DECISION_PROVIDER=openrouter needs no warm-up: nothing is sent.
    """
    env = os.environ if env is None else env
    if (env.get("ARTMIND_DECISION_WARMUP") or "1").strip().casefold() in ("0", "false", "no", "off"):
        return None
    try:
        endpoint = _endpoint(env, None)
    except ValueError as exc:
        logger.warning("decision model warm-up skipped: {}", exc)
        return None
    if endpoint is None:
        return None
    if endpoint["provider"] == "openrouter":
        # A hosted model is always loaded, and a warm-up call would be billed and send a
        # request on every start-up for nothing.
        return None
    url, model = endpoint["url"], endpoint["model"]
    from artmind.query_plan.classify.jev import JevClassifier, _safe_url

    clf = JevClassifier(url, model, api_key=endpoint["api_key"], timeout=WARMUP_TIMEOUT_S)

    def run() -> None:
        started = time.monotonic()
        try:
            clf.warm_up()
        except Exception as exc:
            logger.warning("decision model warm-up failed for {} ({}): {}", _safe_url(url), model, exc)
        else:
            logger.info("decision model {} ready in {:.1f} s", model, time.monotonic() - started)

    thread = threading.Thread(target=run, name="decision-warm-up", daemon=True)
    thread.start()
    return thread
