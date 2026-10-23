"""Decision-model classifier over the Jev API (spec 2026-10-22 §4.4, §7).

One POST per batch of decisions to ARTMIND_DECISION_URL: Ollama's
`/v1/systemone` locally, or a Clef/Jev endpoint. A decision model returns a
probability for every option in one prefill pass, so the same input under a
pinned model gives the same answer. Any failure raises ClassifierUnavailable
and the planner redoes the call lexically. After BREAKER_FAILURES consecutive
failures the endpoint is skipped for BREAKER_COOLDOWN_S, per process, so a
down endpoint does not cost every plan a timeout.

Privacy: the request carries the question, option texts and candidate names.
With a local endpoint nothing leaves the machine; a hosted one receives them.

Daemon notes: `artmind serve` holds a process-wide lock for each proxied CLI
call, so a slow endpoint stalls every other caller; the per-call timeout stays
small (ARTMIND_DECISION_TIMEOUT, default 5 s) and there are no retries. A proxy
timeout makes `_entry` re-run the command in-process, so a retry here would
only multiply the wait. ARTMIND_DECISION_* are read from the environment of
whichever process plans, so the daemon and an in-process run can differ (and
so can their breaker state, which is per process).
"""

from __future__ import annotations

import http.client
import json
import math
import socket
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from loguru import logger

from artmind.query_plan.classify.base import Answer, ClassifierUnavailable, Decision

BREAKER_FAILURES = 3
BREAKER_COOLDOWN_S = 60.0
MAX_BODY_BYTES = 1024 * 1024
_LOG_BODY_CHARS = 500
_PROBABILITY_KEYS = ("probabilities", "probs", "scores")

# Breaker state per (url, model), per process. Closed: open_until is None. After BREAKER_FAILURES
# consecutive failures it opens for BREAKER_COOLDOWN_S; then one probe is let through (half-open)
# and a probe failure reopens it at once, while any success clears it.
_breakers: dict[tuple[str, str], dict] = {}
_breakers_lock = threading.Lock()


def _reset_breakers() -> None:
    """Test isolation only."""
    with _breakers_lock:
        _breakers.clear()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A 3xx is a failure, never a hop: following it would forward the Authorization header."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _open(request, timeout):
    return _OPENER.open(request, timeout=timeout)


def _safe_url(url: str) -> str:
    """scheme://host[:port]/path for log lines: no userinfo, query string or fragment."""
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        port = f":{parts.port}" if parts.port else ""
        return f"{parts.scheme}://{host}{port}{parts.path}"
    except ValueError:
        return "<invalid url>"


def _number(value) -> bool:
    """A finite number in [0, 1] (bool excluded). Raises OverflowError for an int too large for a float."""
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and 0.0 <= value <= 1.0)


def _parse(payload: dict, decision: Decision) -> Answer:
    raw = payload["answers"][decision.name]
    probs = next((raw[k] for k in _PROBABILITY_KEYS if k in raw), None)
    if not isinstance(probs, dict):
        raise ValueError(f"{decision.name}: no probabilities")
    # Keys that are not options are ignored; a bad value for an option is a bad answer.
    given = {k: v for k, v in probs.items() if k in decision.options}
    if not given:
        raise ValueError(f"{decision.name}: no probability for any option")
    for key, value in given.items():
        if not _number(value):
            raise ValueError(f"{decision.name}: bad probability {key!r}={value!r}")
    keys = list(decision.options)
    probabilities = {k: round(float(given.get(k, 0.0)), 4) for k in keys}
    choice = raw.get("choice") or raw.get("answer") or raw.get("label")
    if choice is None:
        choice = max(keys, key=lambda k: (probabilities[k], -keys.index(k)))
    if choice not in decision.options:
        raise ValueError(f"{decision.name}: choice {choice!r} is not an option")
    confidence = raw.get("confidence")
    if confidence is None:
        confidence = probabilities[choice]
    elif not _number(confidence):
        raise ValueError(f"{decision.name}: bad confidence {confidence!r}")
    return Answer(choice, probabilities, round(float(confidence), 4))


class JevClassifier:
    backend = "decision"

    def __init__(self, url: str, model: str, *, api_key: str | None = None, timeout: float = 5.0,
                 clock=time.monotonic):
        self.url = url
        self.model = model
        self.api_key = api_key
        self.timeout = float(timeout)
        self.clock = clock

    @property
    def _key(self) -> tuple[str, str]:
        return (self.url, self.model)

    def _admit(self) -> None:
        """Raise circuit_open while the breaker is open; after the cooldown let one probe through."""
        with _breakers_lock:
            breaker = _breakers.get(self._key)
            if breaker is None or breaker["open_until"] is None:
                return
            now = self.clock()
            if now < breaker["open_until"]:
                raise ClassifierUnavailable("circuit_open")
            # Half-open: this caller is the probe; others see the circuit open until it finishes.
            breaker["open_until"] = now + self.timeout + 1.0

    def _failure(self, reason: str, body: bytes | str | None = None) -> ClassifierUnavailable:
        if reason == "bad_answer" and body is not None:
            text = body.decode("utf-8", "replace") if isinstance(body, bytes) else body
            logger.debug("decision classifier: unusable answer from {} ({}): {}", _safe_url(self.url), self.model,
                         text[:_LOG_BODY_CHARS])
        with _breakers_lock:
            breaker = _breakers.setdefault(self._key, {"failures": 0, "open_until": None})
            breaker["failures"] += 1
            if breaker["failures"] >= BREAKER_FAILURES:
                # One short of the threshold, so a failed probe reopens the circuit immediately.
                breaker["failures"] = BREAKER_FAILURES - 1
                breaker["open_until"] = self.clock() + BREAKER_COOLDOWN_S
        return ClassifierUnavailable(reason)

    def _success(self) -> None:
        with _breakers_lock:
            _breakers.pop(self._key, None)

    def request_body(self, question: str, decisions: list[Decision]) -> dict:
        return {
            "model": self.model,
            "state": {"question": question},
            "questions": {
                d.name: {"type": d.type, "instructions": d.instructions, "criteria": dict(d.options)}
                for d in decisions
            },
        }

    def _request(self, question: str, decisions: list[Decision]) -> urllib.request.Request:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return urllib.request.Request(
            self.url, data=json.dumps(self.request_body(question, decisions)).encode("utf-8"),
            headers=headers, method="POST",
        )

    def warm_up(self) -> None:
        """One tiny request that loads the model; outside the breaker, so a failure here never
        skips the endpoint for real plans. Raises on any failure (the caller logs it)."""
        decision = Decision("warmup", "Pick one.", {"a": "A", "b": "B"}, default="a")
        with _open(self._request("warm up", [decision]), self.timeout) as response:
            response.read(MAX_BODY_BYTES + 1)

    def decide(self, question: str, decisions: list[Decision]) -> dict[str, Answer]:
        self._admit()
        request = self._request(question, decisions)
        raw_body = b""
        try:
            with _open(request, self.timeout) as response:
                raw_body = response.read(MAX_BODY_BYTES + 1)
            if len(raw_body) > MAX_BODY_BYTES:
                raise self._failure("bad_answer", raw_body)
            payload = json.loads(raw_body.decode("utf-8"))
        except ClassifierUnavailable:
            raise
        except urllib.error.HTTPError as exc:
            try:
                exc.close()
            except Exception:
                pass
            raise self._failure(f"http_{exc.code}") from exc
        except urllib.error.URLError as exc:
            timed_out = isinstance(exc.reason, (TimeoutError, socket.timeout))
            raise self._failure("timeout" if timed_out else "unreachable") from exc
        except (TimeoutError, socket.timeout) as exc:
            raise self._failure("timeout") from exc
        except (http.client.HTTPException, OSError) as exc:
            raise self._failure("unreachable") from exc
        except ValueError as exc:
            raise self._failure("bad_answer", raw_body) from exc
        try:
            answers = {d.name: _parse(payload, d) for d in decisions}
        except (KeyError, TypeError, ValueError, AttributeError, OverflowError) as exc:
            raise self._failure("bad_answer", raw_body) from exc
        self._success()
        return answers
