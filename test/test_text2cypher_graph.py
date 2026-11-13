import pytest

from artmind import text2cypher as t2c


# ── cypher_literal ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("value, expected", [
    (None, "null"),
    (True, "true"),
    (False, "false"),
    (3, "3"),
    (2.5, "2.5"),
    ("abc", "'abc'"),
    ("it's", "'it\\'s'"),
    ("back\\slash", "'back\\\\slash'"),
    ("a\nb", "'a\\nb'"),
    (["x", 1], "['x', 1]"),
    ({"k": "v"}, "{`k`: 'v'}"),
])
def test_cypher_literal(value, expected):
    assert t2c.cypher_literal(value) == expected


def test_cypher_literal_quote_injection_stays_inside_string():
    out = t2c.cypher_literal("x' OR 1=1 //")
    assert out == "'x\\' OR 1=1 //'"


def test_cypher_literal_backslash_before_quote_is_not_an_escape_for_the_quote():
    # value  \'  must become  \\\'  (escaped backslash, escaped quote)
    assert t2c.cypher_literal("\\'") == "'\\\\\\''"


def test_cypher_literal_dict_key_backtick_escaped():
    assert t2c.cypher_literal({"a`b": 1}) == "{`a``b`: 1}"


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), object(), {1, 2}])
def test_cypher_literal_refuses_unrenderable(bad):
    with pytest.raises(ValueError):
        t2c.cypher_literal(bad)


# ── inline_parameters ────────────────────────────────────────────────────────

def test_inline_parameters_replaces_all_occurrences():
    q = "MATCH (n) WHERE n._domain IN $domains AND n.x = $x AND n.y = $domains RETURN n"
    out = t2c.inline_parameters(q, {"domains": ["a", "b.c"], "x": 5})
    assert out == "MATCH (n) WHERE n._domain IN ['a', 'b.c'] AND n.x = 5 AND n.y = ['a', 'b.c'] RETURN n"


def test_inline_parameters_missing_value_raises():
    with pytest.raises(ValueError, match=r"\$asOf"):
        t2c.inline_parameters("RETURN $asOf", {"domains": ["a"]})


def test_inline_parameters_does_not_rescan_substituted_text():
    # a value that itself looks like a parameter must not be substituted again
    out = t2c.inline_parameters("RETURN $a, $b", {"a": "$b", "b": 1})
    assert out == "RETURN '$b', 1"


# ── browser_deep_link ────────────────────────────────────────────────────────

def test_deep_link_builds_edit_command():
    url = t2c.browser_deep_link("http://127.0.0.1:7476/browser/", "MATCH (n) RETURN n LIMIT 5")
    assert url == "http://127.0.0.1:7476/browser/?cmd=edit&arg=MATCH%20%28n%29%20RETURN%20n%20LIMIT%205"


def test_deep_link_drops_existing_query_and_encodes_hash_and_ampersand():
    url = t2c.browser_deep_link("http://h:1/browser/?x=1#frag", "RETURN '#&'")
    assert url.startswith("http://h:1/browser/?cmd=edit&arg=")
    assert "%23%26" in url and "#" not in url and "x=1" not in url


@pytest.mark.parametrize("unset", [None, "", "   "])
def test_deep_link_none_when_unconfigured(unset):
    assert t2c.browser_deep_link(unset, "RETURN 1") is None


@pytest.mark.parametrize("bad", ["javascript:alert(1)", "file:///etc/passwd", "127.0.0.1:7476/browser/", "http://"])
def test_deep_link_rejects_non_http_urls(bad):
    with pytest.raises(ValueError):
        t2c.browser_deep_link(bad, "RETURN 1")


# ── graph-mode guard ─────────────────────────────────────────────────────────

def test_prepare_graph_cypher_appends_limit_and_strips_semicolon():
    assert t2c._prepare_graph_cypher("MATCH (n) RETURN n;  ") == "MATCH (n) RETURN n\nLIMIT 100"


def test_prepare_graph_cypher_keeps_existing_limit():
    q = "MATCH (n) RETURN n LIMIT 7"
    assert t2c._prepare_graph_cypher(q) == q


def test_prepare_graph_cypher_leaves_union_alone():
    q = "MATCH (a) RETURN a UNION MATCH (b) RETURN b"
    assert t2c._prepare_graph_cypher(q) == q


def test_prepare_graph_cypher_refuses_call():
    with pytest.raises(ValueError, match="CALL"):
        t2c._prepare_graph_cypher("CALL db.labels() YIELD label RETURN label")


def test_graph_mode_prompt_only_when_asked():
    args = ("q", "schema", "entities", ["d"])
    assert "GRAPH MODE" not in t2c.build_text2cypher_prompt(*args)
    assert "GRAPH MODE" in t2c.build_text2cypher_prompt(*args, graph_mode=True)


def test_generate_cypher_graph_mode_limits_and_refuses_call(monkeypatch):
    monkeypatch.setattr(t2c, "load_env", lambda: {})
    monkeypatch.setattr(t2c, "resolve_llm_model", lambda env, model=None: "m")
    monkeypatch.setattr(t2c, "graph_metadata", lambda d: {"rows": []})
    monkeypatch.setattr(t2c, "entity_listing", lambda d: {"rows": []})
    reply = {"v": '{"cypher": "MATCH (n:Entity) WHERE n._domain IN $domains RETURN n", "parameters": {}}'}
    monkeypatch.setattr(t2c, "call_llm", lambda model, prompt: reply["v"])

    out = t2c.generate_cypher("q", "d", graph_mode=True)
    assert out["cypher"].endswith("LIMIT 100") and out["parameters"]["domains"] == ["d"]
    # table mode is unchanged: no limit appended
    assert t2c.generate_cypher("q", "d")["cypher"].endswith("RETURN n")

    reply["v"] = '{"cypher": "CALL db.labels() YIELD label WITH label MATCH (n) WHERE n._domain IN $domains RETURN n", "parameters": {}}'
    with pytest.raises(ValueError, match="CALL"):
        t2c.generate_cypher("q", "d", graph_mode=True)
