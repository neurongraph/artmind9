"""Intent for `query plan` (spec 2026-11-05 planner v2, section 3).

Eight intents, their lexical cues in `intents.yaml`, and the rules that put the previous
turn to use: a context-only reply inherits the previous offer, and a status cue with an
open job is `status`. Standalone: the planner integrates it separately.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from artmind.query_plan.classify.base import Answer, Decision
from artmind.query_plan.classify.lexical import Facts, answer as lexical_answer, content_tokens, route_scores

INTENTS_FILE = Path(__file__).with_name("intents.yaml")
INTENT_IDS = ("retrieve", "follow_up_format", "follow_up_why", "curate", "update", "meta", "status", "ops")
DEFAULT_INTENT = "retrieve"
MAX_DESCRIPTION = 300
PRONOUNS = frozenset({"he", "she", "they", "it", "that", "them", "his", "her", "their", "this"})

_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_PUNCT_RE = re.compile(r"[^\w\s'-]|_")
_WORD_RE = re.compile(r"[a-z0-9']+")


class IntentError(ValueError):
    """intents.yaml or a `previous` block breaks its contract."""


@dataclass(frozen=True)
class Intent:
    id: str
    description: str
    triggers: tuple[str, ...]
    needs: tuple[str, ...] = ()  # keeps the shape lexical.route_scores reads


@dataclass(frozen=True)
class IntentSpec:
    intents: tuple[Intent, ...]
    affirmatives: tuple[str, ...]

    def get(self, intent_id: str) -> Intent:
        return next(i for i in self.intents if i.id == intent_id)


def _normalise(text: str) -> str:
    return " ".join(_PUNCT_RE.sub(" ", text.casefold().replace("’", "'")).split())


def parse_intents(data) -> IntentSpec:
    if not isinstance(data, dict):
        raise IntentError("intents.yaml must be a mapping")
    if data.get("version") != 1:
        raise IntentError("intents.yaml: version must be 1")
    raw = data.get("intents")
    if not isinstance(raw, list) or not raw:
        raise IntentError("intents.yaml: 'intents' must be a non-empty list")
    out: list[Intent] = []
    seen: set[str] = set()
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise IntentError(f"intents[{i}] must be a mapping")
        iid = item.get("id")
        if not isinstance(iid, str) or not _ID_RE.match(iid):
            raise IntentError(f"intents[{i}]: id must be a lowercase identifier")
        if iid in seen:
            raise IntentError(f"intents: duplicate id {iid!r}")
        seen.add(iid)
        if iid not in INTENT_IDS:
            raise IntentError(f"intent {iid!r} is not one of {', '.join(INTENT_IDS)}")
        desc = item.get("description")
        if not isinstance(desc, str) or not desc.strip():
            raise IntentError(f"intent {iid!r}: description must be a non-empty string")
        if len(desc.strip()) > MAX_DESCRIPTION:
            raise IntentError(f"intent {iid!r}: description is over {MAX_DESCRIPTION} characters")
        trig = item.get("triggers")
        trig = [] if trig is None else trig
        if not isinstance(trig, list) or not all(isinstance(t, str) and t.strip() for t in trig):
            raise IntentError(f"intent {iid!r}: triggers must be a list of non-empty strings")
        if iid == DEFAULT_INTENT and trig:
            raise IntentError(f"intent {DEFAULT_INTENT!r} is the default and takes no triggers")
        out.append(Intent(iid, desc.strip(), tuple(t.strip() for t in trig)))
    missing = [i for i in INTENT_IDS if i not in seen]
    if missing:
        raise IntentError(f"intents.yaml is missing intent(s): {', '.join(missing)}")
    aff = data.get("affirmatives")
    if not isinstance(aff, list) or not aff or not all(isinstance(a, str) and a.strip() for a in aff):
        raise IntentError("intents.yaml: 'affirmatives' must be a non-empty list of strings")
    order = {iid: n for n, iid in enumerate(INTENT_IDS)}
    out.sort(key=lambda i: order[i.id])
    return IntentSpec(tuple(out), tuple(_normalise(a) for a in aff))


@lru_cache(maxsize=1)
def load_intents() -> IntentSpec:
    try:
        data = yaml.safe_load(INTENTS_FILE.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise IntentError(f"cannot read {INTENTS_FILE.name}: {exc}") from exc
    return parse_intents(data)


# --- previous turn -----------------------------------------------------------------------

@dataclass(frozen=True)
class PreviousEntity:
    id: str
    name: str


@dataclass(frozen=True)
class Previous:
    intent: str | None = None
    route: str | None = None
    question: str | None = None
    offer: str | None = None
    entities: tuple[PreviousEntity, ...] = ()
    documents: tuple[str, ...] = ()
    open_job: str | None = None


def _opt_str(data: dict, key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise IntentError(f"previous.{key} must be a string or null")
    return value.strip() or None


def parse_previous(source) -> Previous:
    """A JSON text, or an already-parsed object, into a `Previous`. Unknown keys are ignored."""
    data = source
    if isinstance(source, (str, bytes)):
        try:
            data = json.loads(source)
        except json.JSONDecodeError as exc:
            raise IntentError(f"previous is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise IntentError("previous must be a JSON object")
    intent = _opt_str(data, "intent")
    if intent is not None and intent not in INTENT_IDS:
        raise IntentError(f"previous.intent {intent!r} is not one of {', '.join(INTENT_IDS)}")
    ents = data.get("entities") or []
    if not isinstance(ents, list):
        raise IntentError("previous.entities must be a list of {id, name}")
    entities = []
    for n, e in enumerate(ents):
        if not isinstance(e, dict) or not isinstance(e.get("id"), str) or not isinstance(e.get("name"), str):
            raise IntentError(f"previous.entities[{n}] must be an object with string id and name")
        entities.append(PreviousEntity(e["id"], e["name"]))
    docs = data.get("documents") or []
    if not isinstance(docs, list) or not all(isinstance(d, str) for d in docs):
        raise IntentError("previous.documents must be a list of strings")
    return Previous(intent, _opt_str(data, "route"), _opt_str(data, "question"), _opt_str(data, "offer"),
                    tuple(entities), tuple(docs), _opt_str(data, "open_job"))


def load_previous(path: str, stdin=None) -> Previous:
    """`path` is a JSON file, or `-` for stdin (`stdin` defaults to sys.stdin)."""
    if path == "-":
        text = (stdin if stdin is not None else sys.stdin).read()
    else:
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            raise IntentError(f"cannot read previous file {path!r}: {exc}") from exc
    return parse_previous(text)


# --- decisions ---------------------------------------------------------------------------

def is_context_only(question: str) -> bool:
    """No content words after stopwords, or only affirmatives ("yes", "go on", "yes please")."""
    if not content_tokens(question, drop_capitalised=False):
        return True
    affirmatives = load_intents().affirmatives
    norm = _normalise(question)
    if norm in affirmatives:
        return True
    vocab = {w for a in affirmatives for w in a.split()}
    words = _WORD_RE.findall(norm)
    return bool(words) and all(w in vocab for w in words)


def _scores(question: str) -> dict[str, float]:
    return route_scores(load_intents().intents, Facts(question.casefold(), 0, None, False, False))


def intent_decision(question: str, previous: Previous | None = None) -> Decision:
    """The `intent` decision, for the batched classifier call ahead of `route`."""
    spec = load_intents()
    return Decision(
        name="intent",
        instructions="Pick what the user's message asks for. Use retrieve unless it clearly is one of the others.",
        options={i.id: i.description for i in spec.intents},
        lexical_scores=_scores(question),
        default=DEFAULT_INTENT,
    )


@dataclass(frozen=True)
class IntentResult:
    intent: str
    confidence: float
    source: str  # previous_offer | previous_intent | status_open_job | classifier | lexical
    inherited_offer: str | None = None
    previous_used: bool = False
    alternatives: tuple[tuple[str, float], ...] = ()


def _alternatives(probs: dict[str, float], chosen: str) -> tuple[tuple[str, float], ...]:
    rest = sorted(((k, p) for k, p in probs.items() if k != chosen and p > 0), key=lambda kv: (-kv[1], kv[0]))
    return tuple(rest[:3])


def decide_intent(question: str, previous: Previous | None, answer: Answer | None = None) -> IntentResult:
    """The classifier's answer (or the lexical one) with the previous turn's rules on top."""
    scores = _scores(question)
    if previous is not None and previous.open_job and scores.get("status", 0.0) > 0:
        return IntentResult("status", 1.0, "status_open_job", None, True)
    if previous is not None and is_context_only(question):
        if previous.offer:
            return IntentResult("retrieve", 1.0, "previous_offer", previous.offer, True)
        if previous.intent:
            return IntentResult(previous.intent, 1.0, "previous_intent", None, True)
    if answer is not None:
        return IntentResult(answer.choice, answer.confidence, "classifier", None, False,
                            _alternatives(answer.probabilities, answer.choice))
    lex = lexical_answer(intent_decision(question, previous))
    return IntentResult(lex.choice, lex.confidence, "lexical", None, False,
                        _alternatives(lex.probabilities, lex.choice))


def uses_previous_subjects(question: str, mentions_count: int) -> bool:
    """A pronoun and no named mention: the question is about the previous turn's subjects (spec 4.6)."""
    if mentions_count > 0:
        return False
    words = set(_WORD_RE.findall(question.casefold().replace("’", "'")))
    return bool(words & PRONOUNS)
