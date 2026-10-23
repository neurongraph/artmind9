"""The Jev-API decision classifier (spec 2026-10-22 §4.4, §7). No network: urlopen is faked."""

import http.client
import io
import json
import socket
import urllib.error
import urllib.request

import pytest
from loguru import logger
from _fake_credentials import FAKE_CREDENTIAL

from artmind.query_plan import planner
from artmind.query_plan.classify import ClassifierUnavailable, Decision, select_classifier
from artmind.query_plan import classify
from artmind.query_plan.classify import jev

DECISIONS = [
    Decision("view", "Which saved query?", {"visits_by_year": "Visits ...", "none": "No saved query"},
             {"visits_by_year": 5.0}, default="none", default_confidence=1.0, none_mass=1.0),
    Decision("route", "What kind of question?", {"aggregate": "A count", "search": "Anything else"},
             {"aggregate": 1.5}, default="search", default_confidence=0.5, none_mass=0.5),
]


@pytest.fixture(autouse=True)
def _fresh_breakers():
    jev._reset_breakers()
    yield
    jev._reset_breakers()


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _serve(monkeypatch, *, answers=None, error=None):
    sent = {}

    def fake_urlopen(request, timeout):
        sent.update(url=request.full_url, body=json.loads(request.data), headers=dict(request.headers),
                    timeout=timeout)
        if error is not None:
            raise error
        return _Response(json.dumps({"model": "nimble", "answers": answers}).encode())

    monkeypatch.setattr(jev, "_open", fake_urlopen)
    return sent


def _clf(**kw):
    return jev.JevClassifier("http://localhost:11434/v1/systemone", "nimble", **kw)


def test_the_request_has_the_jev_shape(monkeypatch):
    sent = _serve(monkeypatch, answers={
        "view": {"choice": "visits_by_year", "probabilities": {"visits_by_year": 0.9, "none": 0.1}, "confidence": 0.9},
        "route": {"choice": "aggregate", "probabilities": {"aggregate": 0.7, "search": 0.3}, "confidence": 0.7},
    })
    answers = _clf(api_key=FAKE_CREDENTIAL, timeout=2.5).decide("How many visits?", DECISIONS)
    assert sent["body"] == {
        "model": "nimble",
        "state": {"question": "How many visits?"},
        "questions": {
            "view": {"type": "choice", "instructions": "Which saved query?",
                     "criteria": {"visits_by_year": "Visits ...", "none": "No saved query"}},
            "route": {"type": "choice", "instructions": "What kind of question?",
                      "criteria": {"aggregate": "A count", "search": "Anything else"}},
        },
    }
    assert sent["headers"]["Authorization"] == f"Bearer {FAKE_CREDENTIAL}"
    assert sent["timeout"] == 2.5
    assert answers["view"].choice == "visits_by_year" and answers["view"].confidence == 0.9


def test_a_missing_choice_is_the_most_probable_option(monkeypatch):
    _serve(monkeypatch, answers={
        "view": {"probabilities": {"visits_by_year": 0.2, "none": 0.8}},
        "route": {"probabilities": {"aggregate": 0.6, "search": 0.4}},
    })
    answers = _clf().decide("q", DECISIONS)
    assert answers["view"].choice == "none" and answers["view"].confidence == 0.8


@pytest.mark.parametrize("answers", [
    {"view": {"choice": "elsewhere", "probabilities": {"none": 1.0}}, "route": {"probabilities": {"search": 1.0}}},
    {"view": {"probabilities": {"none": "high"}}, "route": {"probabilities": {"search": 1.0}}},
    {"route": {"probabilities": {"search": 1.0}}},
])
def test_an_unusable_answer_is_bad_answer(monkeypatch, answers):
    _serve(monkeypatch, answers=answers)
    with pytest.raises(ClassifierUnavailable) as caught:
        _clf().decide("q", DECISIONS)
    assert caught.value.reason == "bad_answer"


@pytest.mark.parametrize("error, reason", [
    (socket.timeout("timed out"), "timeout"),
    (urllib.error.URLError(socket.timeout("timed out")), "timeout"),
    (urllib.error.URLError(ConnectionRefusedError()), "unreachable"),
    (urllib.error.HTTPError("http://x", 500, "boom", {}, None), "http_500"),
])
def test_transport_failures_have_a_reason(monkeypatch, error, reason):
    _serve(monkeypatch, error=error)
    with pytest.raises(ClassifierUnavailable) as caught:
        _clf().decide("q", DECISIONS)
    assert caught.value.reason == reason


def test_the_breaker_opens_after_three_failures_and_closes_after_the_cooldown(monkeypatch):
    now = [100.0]
    _serve(monkeypatch, error=socket.timeout("timed out"))
    clf = _clf(clock=lambda: now[0])
    for _ in range(3):
        with pytest.raises(ClassifierUnavailable):
            clf.decide("q", DECISIONS)
    with pytest.raises(ClassifierUnavailable) as caught:
        clf.decide("q", DECISIONS)
    assert caught.value.reason == "circuit_open"
    now[0] += jev.BREAKER_COOLDOWN_S + 1
    with pytest.raises(ClassifierUnavailable) as caught:
        clf.decide("q", DECISIONS)
    assert caught.value.reason == "timeout"


def test_the_planner_falls_back_to_lexical(monkeypatch):
    class Down:
        backend, model = "decision", "nimble"

        def decide(self, question, decisions):
            raise ClassifierUnavailable("timeout")

    warnings: list[str] = []
    calls = planner._Calls(Down(), warnings)
    answers = calls.decide("How often?", DECISIONS)
    assert answers["view"].choice == "visits_by_year"
    assert calls.fell_back and calls.reason == "timeout" and calls.calls == 1
    assert warnings == ["decision classifier unavailable (timeout); used the lexical classifier"]


def test_select_classifier_reads_the_endpoint_settings():
    env = {"ARTMIND_DECISION_URL": "http://localhost:11434/v1/systemone", "ARTMIND_DECISION_MODEL": "nimble",
           "ARTMIND_DECISION_TIMEOUT": "3"}
    clf, reason = select_classifier("auto", "auto", None, env=env)
    assert (clf.backend, clf.model, clf.timeout, reason) == ("decision", "nimble", 3.0, None)
    clf, _ = select_classifier("auto", "auto", "tev1", env=env)
    assert clf.model == "tev1"
    clf, reason = select_classifier("auto", "lexical", None, env=env)
    assert (clf.backend, reason) == ("lexical", "domain_config")
    with pytest.raises(ValueError, match="ARTMIND_DECISION_URL"):
        select_classifier("decision", "auto", None, env={})
    with pytest.raises(ValueError, match="ARTMIND_DECISION_MODEL"):
        select_classifier("auto", "auto", None, env={"ARTMIND_DECISION_URL": "http://x"})


def _ok(probs=None):
    return {"view": {"probabilities": {"none": 1.0}}, "route": {"probabilities": probs or {"search": 1.0}}}


def _fail_n(monkeypatch, n, clf, error=None):
    _serve(monkeypatch, error=error or socket.timeout("timed out"))
    for _ in range(n):
        with pytest.raises(ClassifierUnavailable):
            clf.decide("q", DECISIONS)


# -- no redirects ------------------------------------------------------------------------------

def test_a_redirect_is_an_error_and_the_opener_never_follows_one(monkeypatch):
    handler = jev._NoRedirect()
    assert handler.redirect_request(None, None, 302, "Found", {}, "http://elsewhere/") is None
    assert any(isinstance(h, jev._NoRedirect) for h in jev._OPENER.handlers)
    _serve(monkeypatch, error=urllib.error.HTTPError("http://x", 302, "Found", {}, None))
    with pytest.raises(ClassifierUnavailable) as caught:
        _clf(api_key=FAKE_CREDENTIAL).decide("q", DECISIONS)
    assert caught.value.reason == "http_302"


def test_the_http_error_is_closed(monkeypatch):
    closed = []
    error = urllib.error.HTTPError("http://x", 503, "down", {}, io.BytesIO(b"body"))
    error.close = lambda: closed.append(True)
    _serve(monkeypatch, error=error)
    with pytest.raises(ClassifierUnavailable) as caught:
        _clf().decide("q", DECISIONS)
    assert caught.value.reason == "http_503" and closed == [True]


# -- failures never leak past ClassifierUnavailable --------------------------------------------

@pytest.mark.parametrize("error", [
    http.client.IncompleteRead(b"par"),
    http.client.BadStatusLine("junk"),
    http.client.RemoteDisconnected("closed"),
    ConnectionResetError(),
])
def test_http_client_errors_are_unreachable(monkeypatch, error):
    _serve(monkeypatch, error=error)
    with pytest.raises(ClassifierUnavailable) as caught:
        _clf().decide("q", DECISIONS)
    assert caught.value.reason == "unreachable"


def test_an_integer_too_large_for_a_float_is_bad_answer(monkeypatch):
    _serve(monkeypatch, answers=_ok({"search": 10**400}))
    with pytest.raises(ClassifierUnavailable) as caught:
        _clf().decide("q", DECISIONS)
    assert caught.value.reason == "bad_answer"


def test_an_oversized_body_is_bad_answer(monkeypatch):
    monkeypatch.setattr(jev, "_open", lambda request, timeout: _Response(b" " * (jev.MAX_BODY_BYTES + 1)))
    with pytest.raises(ClassifierUnavailable) as caught:
        _clf().decide("q", DECISIONS)
    assert caught.value.reason == "bad_answer"


def test_a_body_that_is_not_json_is_bad_answer(monkeypatch):
    monkeypatch.setattr(jev, "_open", lambda request, timeout: _Response(b"<html>nope</html>"))
    with pytest.raises(ClassifierUnavailable) as caught:
        _clf().decide("q", DECISIONS)
    assert caught.value.reason == "bad_answer"


# -- the breaker -------------------------------------------------------------------------------

def test_a_success_resets_the_consecutive_failure_count(monkeypatch):
    clf = _clf()
    _fail_n(monkeypatch, 2, clf)
    _serve(monkeypatch, answers=_ok())
    clf.decide("q", DECISIONS)
    _fail_n(monkeypatch, 2, clf)
    with pytest.raises(ClassifierUnavailable) as caught:
        clf.decide("q", DECISIONS)
    assert caught.value.reason == "timeout"


def test_after_the_cooldown_one_probe_runs_and_a_failed_probe_reopens_at_once(monkeypatch):
    now = [100.0]
    clf = _clf(clock=lambda: now[0])
    _fail_n(monkeypatch, 3, clf)
    now[0] += jev.BREAKER_COOLDOWN_S + 1
    with pytest.raises(ClassifierUnavailable) as caught:
        clf.decide("q", DECISIONS)
    assert caught.value.reason == "timeout"
    with pytest.raises(ClassifierUnavailable) as caught:
        clf.decide("q", DECISIONS)
    assert caught.value.reason == "circuit_open"
    now[0] += jev.BREAKER_COOLDOWN_S + 1
    _serve(monkeypatch, answers=_ok())
    clf.decide("q", DECISIONS)
    clf.decide("q", DECISIONS)


def test_while_a_probe_is_running_other_calls_see_the_circuit_open(monkeypatch):
    now = [100.0]
    clf = _clf(clock=lambda: now[0])
    _fail_n(monkeypatch, 3, clf)
    now[0] += jev.BREAKER_COOLDOWN_S + 1
    inner = []

    def probe(request, timeout):
        with pytest.raises(ClassifierUnavailable) as caught:
            clf.decide("q", DECISIONS)
        inner.append(caught.value.reason)
        return _Response(json.dumps({"answers": _ok()}).encode())

    monkeypatch.setattr(jev, "_open", probe)
    clf.decide("q", DECISIONS)
    assert inner == ["circuit_open"]


def test_the_breaker_is_shared_by_url_and_model(monkeypatch):
    _fail_n(monkeypatch, 3, _clf())
    with pytest.raises(ClassifierUnavailable) as caught:
        _clf().decide("q", DECISIONS)
    assert caught.value.reason == "circuit_open"
    other = jev.JevClassifier("http://localhost:11434/v1/systemone", "other-model")
    with pytest.raises(ClassifierUnavailable) as caught:
        other.decide("q", DECISIONS)
    assert caught.value.reason == "timeout"


# -- parsing -----------------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [1.5, -0.1, float("nan"), float("inf"), True])
def test_probabilities_must_be_finite_and_in_unit_range(monkeypatch, bad):
    _serve(monkeypatch, answers=_ok({"search": bad}))
    with pytest.raises(ClassifierUnavailable) as caught:
        _clf().decide("q", DECISIONS)
    assert caught.value.reason == "bad_answer"


@pytest.mark.parametrize("bad", [1.5, -1, float("inf"), "high"])
def test_confidence_must_be_finite_and_in_unit_range(monkeypatch, bad):
    answers = _ok()
    answers["route"]["confidence"] = bad
    _serve(monkeypatch, answers=answers)
    with pytest.raises(ClassifierUnavailable) as caught:
        _clf().decide("q", DECISIONS)
    assert caught.value.reason == "bad_answer"


def test_probability_keys_that_are_not_options_are_ignored(monkeypatch):
    _serve(monkeypatch, answers=_ok({"search": 0.9, "invented": 0.1}))
    answers = _clf().decide("q", DECISIONS)
    assert answers["route"].probabilities == {"aggregate": 0.0, "search": 0.9}


@pytest.mark.parametrize("key", ["probabilities", "probs", "scores"])
def test_the_probabilities_key_has_aliases(monkeypatch, key):
    _serve(monkeypatch, answers={"view": {key: {"none": 1.0}}, "route": {key: {"search": 0.8, "aggregate": 0.2}}})
    answers = _clf().decide("q", DECISIONS)
    assert answers["route"].choice == "search" and answers["route"].confidence == 0.8


def test_a_bad_answer_logs_the_truncated_body_and_no_credentials(monkeypatch):
    lines: list[str] = []
    sink = logger.add(lambda m: lines.append(str(m)), level="DEBUG", format="{message}")
    try:
        monkeypatch.setattr(jev, "_open", lambda request, timeout: _Response(b"x" * 900))
        with pytest.raises(ClassifierUnavailable):
            _clf(api_key=FAKE_CREDENTIAL).decide("q", DECISIONS)
    finally:
        logger.remove(sink)
    logged = "".join(lines)
    assert "x" * 500 in logged and "x" * 501 not in logged
    assert FAKE_CREDENTIAL not in logged and "Bearer" not in logged


# -- select_classifier -------------------------------------------------------------------------

@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://host/x", "localhost:11434/v1", "http://"])
def test_the_url_scheme_must_be_http_or_https(url):
    with pytest.raises(ValueError, match="http"):
        select_classifier("auto", "auto", None, env={"ARTMIND_DECISION_URL": url, "ARTMIND_DECISION_MODEL": "m"})


@pytest.mark.parametrize("timeout", ["nan", "inf", "-1", "0", "soon"])
def test_a_bad_timeout_falls_back_to_the_default(timeout):
    env = {"ARTMIND_DECISION_URL": "http://localhost:1/x", "ARTMIND_DECISION_MODEL": "m",
           "ARTMIND_DECISION_TIMEOUT": timeout}
    clf, _ = select_classifier("auto", "auto", None, env=env)
    assert clf.timeout == 5.0


@pytest.mark.parametrize("url, warns", [
    ("http://models.example.test/v1", True),
    ("http://localhost:11434/v1", False),
    ("http://127.0.0.1:11434/v1", False),
    ("http://[::1]:11434/v1", False),
    ("https://models.example.test/v1", False),
])
def test_a_token_over_clear_http_to_a_remote_host_warns(url, warns):
    lines: list[str] = []
    sink = logger.add(lambda m: lines.append(str(m)), level="WARNING", format="{message}")
    try:
        env = {"ARTMIND_DECISION_URL": url, "ARTMIND_DECISION_MODEL": "m", "ARTMIND_DECISION_API_KEY": FAKE_CREDENTIAL}
        clf, _ = select_classifier("auto", "auto", None, env=env)
    finally:
        logger.remove(sink)
    assert clf.api_key == FAKE_CREDENTIAL
    assert bool(lines) is warns
    assert FAKE_CREDENTIAL not in "".join(lines)


def test_a_url_with_userinfo_is_rejected_without_echoing_it():
    url = f"http://user:{FAKE_CREDENTIAL}@localhost:11434/v1"
    with pytest.raises(ValueError, match="credentials") as caught:
        select_classifier("auto", "auto", None, env={"ARTMIND_DECISION_URL": url, "ARTMIND_DECISION_MODEL": "m"})
    assert FAKE_CREDENTIAL not in str(caught.value)


def test_logs_name_the_endpoint_without_its_query_string(monkeypatch):
    lines: list[str] = []
    sink = logger.add(lambda m: lines.append(str(m)), level="DEBUG", format="{message}")
    try:
        monkeypatch.setattr(jev, "_open", lambda request, timeout: _Response(b"not json"))
        clf = jev.JevClassifier("http://localhost:11434/v1/systemone?token=x#frag", "nimble")
        with pytest.raises(ClassifierUnavailable):
            clf.decide("q", DECISIONS)
    finally:
        logger.remove(sink)
    logged = "".join(lines)
    assert "http://localhost:11434/v1/systemone" in logged
    assert "token=x" not in logged and "#frag" not in logged


def test_a_timeout_override_beats_the_environment():
    env = {"ARTMIND_DECISION_URL": "http://localhost:11434/v1/systemone", "ARTMIND_DECISION_MODEL": "nimble",
           "ARTMIND_DECISION_TIMEOUT": "3"}
    clf, _ = select_classifier("auto", "auto", None, env=env, timeout=60.0)
    assert clf.timeout == 60.0


def test_the_planner_passes_its_decision_timeout(monkeypatch):
    seen = {}

    def fake_select(option, domain_choice, model, env=None, timeout=None):
        seen["timeout"] = timeout
        raise ValueError("stop here")

    monkeypatch.setattr(planner, "select_classifier", fake_select)
    monkeypatch.setattr(planner.graph_query, "domains_overview", lambda: {"rows": []})
    with pytest.raises(planner.PlanError):
        planner.plan("How many visits?", ["journal"], decision_timeout=42.0)
    assert seen == {"timeout": 42.0}


WARM_ENV = {"ARTMIND_DECISION_URL": "http://localhost:11434/v1/systemone", "ARTMIND_DECISION_MODEL": "nimble"}


def test_warm_up_sends_one_tiny_request_with_a_long_timeout(monkeypatch):
    sent = _serve(monkeypatch, answers={"warmup": {"choice": "a", "probabilities": {"a": 1.0, "b": 0.0}}})
    thread = classify.warm_up(env=WARM_ENV)
    thread.join(2)
    assert sent["url"] == WARM_ENV["ARTMIND_DECISION_URL"]
    assert sent["body"]["model"] == "nimble" and list(sent["body"]["questions"]) == ["warmup"]
    assert sent["timeout"] == classify.WARMUP_TIMEOUT_S
    assert "Authorization" not in sent["headers"]


def test_a_failed_warm_up_is_logged_and_leaves_the_breaker_closed(monkeypatch):
    _serve(monkeypatch, error=urllib.error.URLError(ConnectionRefusedError()))
    lines = []
    sink = logger.add(lambda m: lines.append(m), level="WARNING")
    try:
        for _ in range(jev.BREAKER_FAILURES):
            classify.warm_up(env=WARM_ENV).join(2)
    finally:
        logger.remove(sink)
    assert any("warm-up failed" in line for line in lines)
    assert jev._breakers == {}


@pytest.mark.parametrize("env", [
    {},
    {"ARTMIND_DECISION_URL": "http://localhost:11434/v1/systemone"},
    {**WARM_ENV, "ARTMIND_DECISION_WARMUP": "0"},
    {**WARM_ENV, "ARTMIND_DECISION_URL": "ftp://localhost/x"},
])
def test_no_warm_up_without_a_usable_endpoint_or_when_turned_off(monkeypatch, env):
    sent = _serve(monkeypatch, answers={})
    assert classify.warm_up(env=env) is None
    assert sent == {}


# -- OpenRouter provider (ARTMIND_DECISION_PROVIDER=openrouter) -------------------------------

OR_ENV = {"ARTMIND_DECISION_PROVIDER": "openrouter", "ARTMIND_DECISION_MODEL": "typesafe/jev-1.14",
          "ARTMIND_OPENROUTER_API_KEY": FAKE_CREDENTIAL,
          # the ollama provider's settings, which openrouter must ignore
          "ARTMIND_DECISION_URL": "http://localhost:11434/v1/systemone",
          "ARTMIND_DECISION_API_KEY": f"{FAKE_CREDENTIAL}-ollama"}


def test_openrouter_sends_the_jev_body_to_its_decisions_api(monkeypatch):
    sent = _serve(monkeypatch, answers={
        "view": {"type": "choice", "choice": "none", "probabilities": {"visits_by_year": 0, "none": 1},
                 "confidence": 1},
        "route": {"type": "choice", "choice": "aggregate", "probabilities": {"aggregate": 0.8, "search": 0.2},
                  "confidence": 0.8},
    })
    clf, reason = select_classifier("decision", "auto", None, env=OR_ENV)
    answers = clf.decide("How many visits?", DECISIONS)
    assert reason is None and clf.model == "typesafe/jev-1.14"
    assert sent["url"] == "https://openrouter.ai/api/alpha/decisions"
    assert sent["headers"]["Authorization"] == f"Bearer {FAKE_CREDENTIAL}"
    assert sent["headers"]["Content-type"] == "application/json"
    assert sent["body"] == {
        "provider": {"data_collection": "deny", "zdr": True},
        "model": "typesafe/jev-1.14",
        "state": {"question": "How many visits?"},
        "questions": {
            "view": {"type": "choice", "instructions": "Which saved query?",
                     "criteria": {"visits_by_year": "Visits ...", "none": "No saved query"}},
            "route": {"type": "choice", "instructions": "What kind of question?",
                      "criteria": {"aggregate": "A count", "search": "Anything else"}},
        },
    }
    # integer probabilities (OpenRouter sends 0 and 1) parse like floats
    assert answers["view"].probabilities == {"visits_by_year": 0.0, "none": 1.0}
    assert answers["route"].choice == "aggregate" and answers["route"].confidence == 0.8


def test_openrouter_extra_fields_cannot_override_the_request():
    clf = jev.JevClassifier("https://openrouter.ai/api/alpha/decisions", "typesafe/jev-1.14",
                            extra_body={"model": "other", "state": "x", "provider": {"zdr": True}})
    body = clf.request_body("q", DECISIONS)
    assert body["model"] == "typesafe/jev-1.14" and body["state"] == {"question": "q"}


def test_openrouter_needs_its_key_and_a_model():
    with pytest.raises(ValueError, match="ARTMIND_OPENROUTER_API_KEY"):
        select_classifier("auto", "auto", None, env={**OR_ENV, "ARTMIND_OPENROUTER_API_KEY": ""})
    with pytest.raises(ValueError, match="ARTMIND_DECISION_MODEL"):
        select_classifier("decision", "auto", None, env={**OR_ENV, "ARTMIND_DECISION_MODEL": ""})
    # a domain config's decision_model is the OpenRouter model
    clf, _ = select_classifier("auto", "auto", "typesafe/jev-1.13", env=OR_ENV)
    assert clf.model == "typesafe/jev-1.13"
    # lexical choices never need an endpoint
    assert select_classifier("lexical", "auto", None, env={"ARTMIND_DECISION_PROVIDER": "openrouter"})[1] is None


def test_an_unknown_provider_is_an_error():
    with pytest.raises(ValueError, match="ARTMIND_DECISION_PROVIDER"):
        select_classifier("auto", "auto", None, env={"ARTMIND_DECISION_PROVIDER": "clef"})


def test_the_default_provider_is_the_url():
    clf, _ = select_classifier("auto", "auto", None, env={**WARM_ENV, "ARTMIND_DECISION_PROVIDER": "Ollama"})
    assert clf.url == WARM_ENV["ARTMIND_DECISION_URL"] and clf.extra_body == {}
    assert "provider" not in clf.request_body("q", DECISIONS)


@pytest.mark.parametrize("error, reason", [
    (urllib.error.HTTPError("https://openrouter.ai/api/alpha/decisions", 402, "Payment Required", {}, None),
     "http_402"),
    (urllib.error.HTTPError("https://openrouter.ai/api/alpha/decisions", 404, "No allowed providers", {}, None),
     "http_404"),
    (urllib.error.HTTPError("https://openrouter.ai/api/alpha/decisions", 302, "Found",
                            {"Location": "https://elsewhere.example/"}, None), "http_302"),
    (socket.timeout("timed out"), "timeout"),
])
def test_openrouter_failures_fall_back_with_a_reason(monkeypatch, error, reason):
    _serve(monkeypatch, error=error)
    clf, _ = select_classifier("decision", "auto", None, env=OR_ENV)
    with pytest.raises(ClassifierUnavailable) as caught:
        clf.decide("q", DECISIONS)
    assert caught.value.reason == reason


def test_openrouter_trips_the_breaker_like_any_endpoint(monkeypatch):
    clf, _ = select_classifier("decision", "auto", None, env=OR_ENV)
    _fail_n(monkeypatch, jev.BREAKER_FAILURES, clf)
    sent = _serve(monkeypatch, answers=_ok())
    with pytest.raises(ClassifierUnavailable, match="circuit_open"):
        clf.decide("q", DECISIONS)
    assert sent == {}


def test_openrouter_is_not_warmed_up(monkeypatch):
    sent = _serve(monkeypatch, answers={})
    assert classify.warm_up(env=OR_ENV) is None
    assert sent == {}


def test_a_half_configured_openrouter_warm_up_is_skipped_with_a_warning(monkeypatch):
    sent = _serve(monkeypatch, answers={})
    lines = []
    sink = logger.add(lambda m: lines.append(m), level="WARNING")
    try:
        assert classify.warm_up(env={**OR_ENV, "ARTMIND_OPENROUTER_API_KEY": ""}) is None
    finally:
        logger.remove(sink)
    assert sent == {} and any("ARTMIND_OPENROUTER_API_KEY" in line for line in lines)
    assert not any(FAKE_CREDENTIAL in line for line in lines)
