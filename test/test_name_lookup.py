"""The per-name lookup between extraction and canonicalization (plan 2026-11-15 task 3)."""
from _neo4j_recorder import Recorder

from artmind import canonicalize, ingest
from artmind.observations import aggregate_key, key_string

SCHEMA = {"entity_types": {
    "PRODUCT": {"kind": "recurrent"},
    "INCIDENT": {"kind": "occurrent"},
}}

ENTITIES = [
    {"name": "Zephyr Saver", "entity_class": "PRODUCT"},
    {"name": "Zephyr Saver Plus", "entity_class": "PRODUCT"},
    {"name": "Flood in March", "entity_class": "INCIDENT"},
]


def _respond(exact=(), vector=()):
    def respond(cypher, params):
        if "VECTOR INDEX entity_name_embedding" in cypher:
            return list(vector)
        if "coalesce(e.aliases, [])" in cypher:
            return list(exact)
        return []
    return respond


def _lookup(session, **kw):
    return canonicalize.lookup_name_candidates(
        session, ENTITIES, domain="bank", schema=SCHEMA, embed_model="m",
        embed=lambda text: [0.1, 0.2], cache=None, **kw)


def test_only_recurrent_names_are_looked_up_and_sent_as_one_batch():
    s = Recorder(_respond())
    _lookup(s)
    (exact_q, ep), = s.queries_containing("coalesce(e.aliases, [])")
    (vec_q, vp), = s.queries_containing("VECTOR INDEX entity_name_embedding")
    assert [r["lower"] for r in ep["rows"]] == ["zephyr saver", "zephyr saver plus"]
    assert {r["entity_class"] for r in vp["rows"]} == {"PRODUCT"}
    assert ep["domain"] == vp["domain"] == "bank"
    assert "e._domain = $domain AND e.entity_class = row.entity_class" in exact_q
    assert "node._domain = $domain AND node.entity_class = row.entity_class" in vec_q
    assert vp["min_sim"] == canonicalize.NAME_LOOKUP_MIN_SIM and vp["top"] == canonicalize.NAME_LOOKUP_TOP


def test_alias_hits_rank_before_vector_hits_and_own_key_is_dropped():
    own = key_string(aggregate_key("Zephyr Saver", "PRODUCT", "bank"))
    s = Recorder(_respond(
        exact=[{"i": 0, "name": "Zephyr Savings Account", "key": "zephyr savings account|PRODUCT|bank", "score": 1.0}],
        vector=[{"i": 0, "name": "Zephyr Saver", "key": own, "score": 0.99},
                {"i": 0, "name": "Zephyr Bonus Saver", "key": "zephyr bonus saver|PRODUCT|bank", "score": 0.91},
                {"i": 0, "name": "Zephyr Savings Account", "key": "zephyr savings account|PRODUCT|bank", "score": 0.95}],
    ))
    out = _lookup(s)
    assert out == {("PRODUCT", "Zephyr Saver"): ["Zephyr Savings Account", "Zephyr Bonus Saver"]}


def test_a_failing_leg_never_raises():
    def boom(cypher, params):
        raise RuntimeError("neo4j down")
    assert _lookup(Recorder(boom)) == {}

    def no_embed(text):
        raise RuntimeError("embed down")
    s = Recorder(_respond(exact=[{"i": 1, "name": "Zephyr Saver+", "key": "k|PRODUCT|bank", "score": 1.0}]))
    out = canonicalize.lookup_name_candidates(s, ENTITIES, domain="bank", schema=SCHEMA, embed_model="m",
                                              embed=no_embed, cache=None)
    assert out == {("PRODUCT", "Zephyr Saver Plus"): ["Zephyr Saver+"]}


def test_names_are_capped():
    s = Recorder(_respond())
    _lookup(s, max_names=1)
    (_q, ep), = s.queries_containing("coalesce(e.aliases, [])")
    assert len(ep["rows"]) == 1


def test_no_recurrent_class_runs_no_query():
    s = Recorder(_respond())
    out = canonicalize.lookup_name_candidates(s, ENTITIES, domain="bank",
                                              schema={"entity_types": {"INCIDENT": {"kind": "occurrent"}}},
                                              embed_model="m", embed=lambda t: [0.1], cache=None)
    assert out == {} and s.calls == []


def test_candidates_reach_the_canonicalization_prompt_under_existing_names():
    names = canonicalize.collect_names(ENTITIES)
    prompt = canonicalize.build_canonicalization_prompt(
        names, [], SCHEMA, candidates={("PRODUCT", "Zephyr Saver"): ["Zephyr Savings Account"]})
    assert 'PRODUCT "Zephyr Saver":' in prompt
    assert "    - Zephyr Savings Account" in prompt
    assert "none yet" not in prompt


def test_lookup_is_off_by_env(monkeypatch):
    monkeypatch.setenv("ARTMIND_NAME_LOOKUP", "off")
    assert canonicalize.name_lookup_enabled() is False
    assert ingest._name_candidates(ENTITIES, domain="bank", schema=SCHEMA, embed_model="m") == {}
    monkeypatch.setenv("ARTMIND_NAME_LOOKUP", "on")
    assert canonicalize.name_lookup_enabled() is True


def test_min_sim_env(monkeypatch):
    monkeypatch.setenv("ARTMIND_NAME_LOOKUP_MIN_SIM", "0.9")
    assert canonicalize.name_lookup_min_sim() == 0.9
    monkeypatch.setenv("ARTMIND_NAME_LOOKUP_MIN_SIM", "nope")
    assert canonicalize.name_lookup_min_sim() == canonicalize.NAME_LOOKUP_MIN_SIM


def test_vocabulary_shows_short_aliases_on_their_own_line():
    text = canonicalize.render_vocabulary([
        {"name": "Zephyr Saver", "entity_class": "PRODUCT",
         "aliases": ["ZS", "zephyr saver", "x" * 80, "Zephyr", "Z-Saver", "Fourth"]},
        {"name": "Other", "entity_class": "PRODUCT"},
    ])
    assert text.splitlines() == [
        "  PRODUCT:",
        "    - Other",
        "    - Zephyr Saver",
        "        also called: ZS | Zephyr | Z-Saver",
    ]
