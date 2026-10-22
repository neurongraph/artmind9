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

import json
import socket
import time
import urllib.error
import urllib.request

from artmind.query_plan.classify.base import Answer, ClassifierUnavailable, Decision

BREAKER_FAILURES = 3
BREAKER_COOLDOWN_S = 60.0
_breakers: dict[str, dict] = {}


def _reset_breakers() -> None:
    """Test isolation only."""
    _breakers.clear()


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _parse(payload: dict, decision: Decision) -> Answer:
    raw = payload["answers"][decision.name]
    probs = raw.get("probabilities")
    if not isinstance(probs, dict) or not probs:
        raise ValueError(f"{decision.name}: no probabilities")
    for key, value in probs.items():
        if key not in decision.options or not _number(value):
            raise ValueError(f"{decision.name}: bad probability {key!r}={value!r}")
    keys = list(decision.options)
    probabilities = {k: round(float(probs.get(k, 0.0)), 4) for k in keys}
    choice = raw.get("choice") or raw.get("answer") or raw.get("label")
    if choice is None:
        choice = max(keys, key=lambda k: (probabilities[k], -keys.index(k)))
    if choice not in decision.options:
        raise ValueError(f"{decision.name}: choice {choice!r} is not an option")
    confidence = raw.get("confidence")
    confidence = round(float(confidence), 4) if _number(confidence) else probabilities[choice]
    return Answer(choice, probabilities, confidence)


class JevClassifier:
    backend = "decision"

    def __init__(self, url: str, model: str, *, api_key: str | None = None, timeout: float = 5.0,
                 clock=time.monotonic):
        self.url = url
        self.model = model
        self.api_key = api_key
        self.timeout = float(timeout)
        self.clock = clock

    def _breaker(self) -> dict:
        return _breakers.setdefault(self.url, {"failures": 0, "open_until": 0.0})

    def _failure(self, reason: str) -> ClassifierUnavailable:
        breaker = self._breaker()
        breaker["failures"] += 1
        if breaker["failures"] >= BREAKER_FAILURES:
            breaker["failures"] = 0
            breaker["open_until"] = self.clock() + BREAKER_COOLDOWN_S
        return ClassifierUnavailable(reason)

    def request_body(self, question: str, decisions: list[Decision]) -> dict:
        return {
            "model": self.model,
            "state": {"question": question},
            "questions": {
                d.name: {"type": d.type, "instructions": d.instructions, "criteria": dict(d.options)}
                for d in decisions
            },
        }

    def decide(self, question: str, decisions: list[Decision]) -> dict[str, Answer]:
        if self.clock() < self._breaker()["open_until"]:
            raise ClassifierUnavailable("circuit_open")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(
            self.url, data=json.dumps(self.request_body(question, decisions)).encode("utf-8"),
            headers=headers, method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise self._failure(f"http_{exc.code}") from exc
        except urllib.error.URLError as exc:
            timed_out = isinstance(exc.reason, (TimeoutError, socket.timeout))
            raise self._failure("timeout" if timed_out else "unreachable") from exc
        except (TimeoutError, socket.timeout) as exc:
            raise self._failure("timeout") from exc
        except OSError as exc:
            raise self._failure("unreachable") from exc
        except ValueError as exc:
            raise self._failure("bad_answer") from exc
        try:
            answers = {d.name: _parse(payload, d) for d in decisions}
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise self._failure("bad_answer") from exc
        self._breaker()["failures"] = 0
        return answers
