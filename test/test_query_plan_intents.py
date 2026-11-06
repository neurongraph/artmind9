import io
import json

import pytest

from artmind.query_plan import intents as I
from artmind.query_plan.classify.base import Answer
from artmind.query_plan.classify.lexical import answer as lex_answer


def lexical(q):
    return lex_answer(I.intent_decision(q)).choice


@pytest.mark.parametrize("q,want", [
    ("Done yet?", "status"),
    ("is it done", "status"),
    ("how far along is it", "status"),
    ("make that a table", "follow_up_format"),
    ("shorter please, add the dates", "follow_up_format"),
    ("why did you miss the second contract", "follow_up_why"),
    ("are you sure about that", "follow_up_why"),
    ("Alpha and Beta are the same person", "curate"),
    ("that is wrong", "curate"),
    ("remember that the vendor moved", "update"),
    ("which model are you", "meta"),
    ("what can you do", "meta"),
    ("ingest the new folder", "ops"),
    ("please reingest that note", "ops"),
    ("who signed the lease in march", "retrieve"),
])
def test_cues(q, want):
    assert lexical(q) == want


def test_anchor_only_at_start():
    assert lexical("what has been done about the lease") == "retrieve"


def test_decision_shape():
    d = I.intent_decision("is it done")
    assert set(d.options) == set(I.INTENT_IDS) and len(d.options) == 8
    assert d.default == "retrieve" and d.name == "intent"
    assert d.lexical_scores["status"] > 0
    assert all(len(v) <= 300 for v in d.options.values())


@pytest.mark.parametrize("q", ["yes", "Yes!", "ok", "go on", "yes please", "do it", "Go ahead.", "", "??"])
def test_context_only_true(q):
    assert I.is_context_only(q)


@pytest.mark.parametrize("q", ["who signed the lease", "yes the lease", "make it shorter"])
def test_context_only_false(q):
    assert not I.is_context_only(q)


def test_context_only_with_offer():
    prev = I.Previous(intent="retrieve", offer="run a full survey")
    r = I.decide_intent("yes please", prev, None)
    assert (r.intent, r.source, r.inherited_offer, r.previous_used) == ("retrieve", "previous_offer", "run a full survey", True)


def test_context_only_without_offer_inherits_intent():
    r = I.decide_intent("go on", I.Previous(intent="follow_up_format"), None)
    assert (r.intent, r.source, r.inherited_offer, r.previous_used) == ("follow_up_format", "previous_intent", None, True)


def test_context_only_no_previous():
    r = I.decide_intent("yes", None, None)
    assert r.intent == "retrieve" and not r.previous_used and r.source == "lexical"


def test_status_with_and_without_open_job():
    assert I.decide_intent("is it done", I.Previous(open_job="job-1"), Answer("retrieve", {"retrieve": 1.0}, 0.6)).intent == "status"
    r = I.decide_intent("how far is it", I.Previous(open_job="job-1"), None)
    assert r.source == "status_open_job"
    r = I.decide_intent("is it done", I.Previous(), Answer("retrieve", {"retrieve": 0.7, "status": 0.3}, 0.7))
    assert (r.intent, r.source) == ("retrieve", "classifier")
    assert r.alternatives == (("status", 0.3),)
    assert I.decide_intent("is it done", None, None).intent == "status"


def test_open_job_without_status_cue():
    r = I.decide_intent("who signed the lease", I.Previous(open_job="j"), None)
    assert r.intent == "retrieve" and r.source == "lexical"


def test_pronoun_helper():
    assert I.uses_previous_subjects("what did she say next", 0)
    assert not I.uses_previous_subjects("what did she say next", 1)
    assert not I.uses_previous_subjects("what did Dana say next", 0)
    assert I.uses_previous_subjects("Tell me about it", 0)


def test_parse_previous_full():
    p = I.parse_previous(json.dumps({
        "intent": "retrieve", "route": "survey", "question": "q", "offer": "more",
        "entities": [{"id": "e1", "name": "Dana"}], "documents": ["a.md"], "open_job": None, "extra": 1}))
    assert p.entities == (I.PreviousEntity("e1", "Dana"),) and p.documents == ("a.md",)
    assert p.offer == "more" and p.open_job is None


@pytest.mark.parametrize("bad", [
    "not json", "[]", '{"intent": "bogus"}', '{"offer": 3}', '{"entities": "x"}',
    '{"entities": [{"id": "e"}]}', '{"documents": [1]}',
])
def test_parse_previous_errors(bad):
    with pytest.raises(I.IntentError):
        I.parse_previous(bad)


def test_load_previous_file_and_stdin(tmp_path):
    f = tmp_path / "p.json"
    f.write_text('{"offer": "go"}')
    assert I.load_previous(str(f)).offer == "go"
    assert I.load_previous("-", stdin=io.StringIO('{"open_job": "j"}')).open_job == "j"
    with pytest.raises(I.IntentError):
        I.load_previous(str(tmp_path / "missing.json"))
    with pytest.raises(I.IntentError):
        I.load_previous("-", stdin=io.StringIO("{"))


def _good():
    return {"version": 1,
            "intents": [{"id": i, "description": "d", "triggers": []} for i in I.INTENT_IDS],
            "affirmatives": ["yes"]}


def test_shipped_yaml_loads():
    spec = I.load_intents()
    assert [i.id for i in spec.intents] == list(I.INTENT_IDS)
    assert "go on" in spec.affirmatives


def test_parse_ok():
    assert len(I.parse_intents(_good()).intents) == 8


@pytest.mark.parametrize("mutate,msg", [
    (lambda d: d.update(version=2), "version"),
    (lambda d: d.update(intents=[]), "non-empty"),
    (lambda d: d["intents"].pop(), "missing"),
    (lambda d: d["intents"].append({"id": "meta", "description": "d"}), "duplicate"),
    (lambda d: d["intents"][0].update(id="Bad Id"), "identifier"),
    (lambda d: d["intents"][1].update(id="other"), "not one of"),
    (lambda d: d["intents"][1].update(description=""), "description"),
    (lambda d: d["intents"][1].update(description="x" * 301), "300"),
    (lambda d: d["intents"][1].update(triggers="a"), "triggers"),
    (lambda d: d["intents"][0].update(triggers=["x"]), "default"),
    (lambda d: d.update(affirmatives=[]), "affirmatives"),
])
def test_parse_errors(mutate, msg):
    d = _good()
    mutate(d)
    with pytest.raises(I.IntentError, match=msg):
        I.parse_intents(d)


@pytest.mark.parametrize("question", [
    "What was the progress on the garden project last spring?",
    "Did the two firms merge in the end?",
    "How far did we drive on the coast trip?",
    "Which clauses talk about rebuilding the old office?",
])
def test_corpus_questions_with_cue_words_stay_retrieve(question):
    assert I.decide_intent(question, None, None).intent == "retrieve"


def test_cues_ignore_case():
    assert I.decide_intent("Remember That the meeting moved", None, None).intent == "update"


@pytest.mark.parametrize("question,intent", [
    ("could you look at the status of that job", "status"),
    ("which embedding model are you running on", "meta"),
    ("propose a same-as for these two", "curate"),
])
def test_round8_shaped_cues(question, intent):
    assert I.decide_intent(question, None, None).intent == intent


def test_more_affirmatives_are_context_only():
    assert I.is_context_only("sounds good, proceed")


# --- D3 / D4 ------------------------------------------------------------------------------

@pytest.mark.parametrize("q,want", [
    ("Approve the merge you suggested", "curate"),
    ("approve Alpha ↔ Beta, reject the others", "curate"),
    ("Reject all the others, they are distinct", "curate"),
    ("Adjudicate the 12 open conflicts", "curate"),
    ("Review the 5 open same-as proposals", "curate"),
    ("I am good with dismissing clusters A and B", "curate"),
    ("these two should also be dismissed", "curate"),
    ("they are two distinct events", "curate"),
    ("all these are different", "curate"),
    ("Alpha and Beta are really the same programme", "curate"),
    ("ask artmind curate to fix the spelling variants", "curate"),
    ("use artmind curate to fix", "curate"),
    ("ok - run the synthesize", "ops"),
    ("run the projection rebuild", "ops"),
    ("run vault sync", "ops"),
    ("can you check the status now", "status"),
    ("is the ingest done?", "status"),
    ("how did the conflict with the landlord end?", "retrieve"),
    ("which proposals did the board approve in 2024?", "retrieve"),
    ("what is different about the new contract?", "retrieve"),
    ("are there any distinct trends?", "retrieve"),
    ("what did the reviewer reject?", "retrieve"),
])
def test_lexical_cues_d4(q, want):
    assert lexical(q) == want


@pytest.mark.parametrize("q", [
    "run as is", "yes do (a) and (b)", "I like your plan, proceed", "option 2", "the others",
    "what you have suggested", "that one", "go ahead", "do it", "these", "dismiss those", "yes", "",
])
def test_refers_to_previous_true(q):
    assert I.refers_to_previous(q)


@pytest.mark.parametrize("q", [
    "what does this policy say about notice periods?",
    "who signed that lease in march",
    "why was this contract terminated early by the vendor and when did that happen",
    "which of the vendors raised option 2 in the long meeting about renewal terms",
])
def test_refers_to_previous_false(q):
    assert not I.refers_to_previous(q)


def test_cap_without_previous_classifier_and_lexical():
    ans = Answer("ops", {"ops": 0.95, "retrieve": 0.05}, 0.95)
    r = I.decide_intent("run as is", None, ans)
    assert (r.intent, r.source, r.confidence, r.no_previous_capped) == ("ops", "classifier", I.NO_PREVIOUS_CAP, True)
    r = I.decide_intent("proceed", None, Answer("status", {"status": 0.3}, 0.3))
    assert r.confidence == 0.3 and r.no_previous_capped  # a lower confidence is kept
    r = I.decide_intent("approve the merge you suggested", None, None)
    assert r.intent == "curate" and r.source == "lexical" and r.confidence <= I.NO_PREVIOUS_CAP and r.no_previous_capped


def test_cap_not_applied_with_previous_or_standalone():
    ans = Answer("ops", {"ops": 0.95}, 0.95)
    r = I.decide_intent("run as is", I.Previous(intent="retrieve"), ans)
    # With a previous turn the reply continues it (no cap): the previous intent, not the classifier's guess.
    assert (r.intent, r.source, r.confidence) == ("retrieve", "previous_intent", 1.0) and not r.no_previous_capped
    r = I.decide_intent("rebuild the index now please", None, Answer("ops", {"ops": 0.9}, 0.9))
    assert r.confidence == 0.9 and not r.no_previous_capped


# ── a reply that points back continues the previous turn ─────────────────────────────────

def test_an_offer_inside_a_curate_turn_continues_curation():
    prev = I.Previous(intent="curate", offer="approve the two merges")
    r = I.decide_intent("yes please", prev, None)
    assert (r.intent, r.source, r.previous_used) == ("curate", "previous_intent", True)


@pytest.mark.parametrize("q", ["run as is", "Yes do (a) and (b)", "I like your plan, proceed accordingly"])
def test_a_pointing_back_reply_inherits_the_previous_intent(q):
    for answer in (None, Answer("retrieve", {"retrieve": 0.9}, 0.9)):
        r = I.decide_intent(q, I.Previous(intent="curate", offer="shall I merge them?"), answer)
        assert (r.intent, r.source) == ("curate", "previous_intent"), q


def test_a_pointing_back_reply_after_a_lookup_takes_the_offer():
    r = I.decide_intent("go ahead", I.Previous(intent="retrieve", offer="run a full survey"), None)
    assert (r.intent, r.source, r.inherited_offer) == ("retrieve", "previous_offer", "run a full survey")


def test_a_reply_with_its_own_cue_is_classified_on_its_own():
    r = I.decide_intent("approve the others", I.Previous(intent="retrieve", offer="list them"), None)
    assert r.intent == "curate" and r.source == "lexical"
