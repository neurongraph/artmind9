from artmind import hints


def test_join_next():
    assert hints.join_next("a", "b") == "a; b"
    assert hints.join_next(None, "b") == "b"
    assert hints.join_next("a", None) == "a"
    assert hints.join_next(None, None) is None


def test_pattern6_empty_placeholders_and_classes():
    assert hints.pattern6_empty({"rows": []}, ["bank", "kyc"], "Ann", "Bob") == (
        'artmind query graph pattern5 --domain bank --domain kyc --entityClass1 <CLASS1> '
        '--entityClass2 <CLASS2> --entityName1 "Ann" --entityName2 "Bob" --mode shortest --compact'
    )
    assert hints.pattern6_empty({"rows": []}, ["bank"], "Ann", "Bob", "PERSON", "TEAM") == (
        'artmind query graph pattern5 --domain bank --entityClass1 PERSON '
        '--entityClass2 TEAM --entityName1 "Ann" --entityName2 "Bob" --mode shortest --compact'
    )
    assert hints.pattern6_empty({"rows": [{"x": 1}]}, ["bank"], "Ann", "Bob") is None


def test_pattern1_empty():
    assert hints.pattern1_empty({"rows": []}, ["bank"]) == (
        'artmind query graph entity-listing --domain bank --nameFilter "<word>" --compact'
    )
    assert hints.pattern1_empty({"rows": [1]}, ["bank"]) is None


def test_hierarchy_empty():
    payload = {"summary": {"total": 0}, "root": {"entity_class": "ORG_UNIT"}}
    assert hints.hierarchy_empty(payload, ["bank"], 'who "reports"?') == (
        'artmind query graph pattern1 --domain bank --entityClass ORG_UNIT --compact "who \\"reports\\"?"'
    )
    assert hints.hierarchy_empty({"summary": {"total": 3}, "root": {}}, ["bank"], "q") is None
    assert hints.hierarchy_empty({"edge_candidates": []}, ["bank"], "q") is None


def test_resolve_key_empty():
    assert hints.resolve_key_empty({"candidates": []}, ["bank"], "acme", 5) == (
        'artmind query resolve-key --domain bank --topK 25 --compact "acme"'
    )
    assert hints.resolve_key_empty({"candidates": []}, ["bank"], "acme", 25) == (
        'artmind query entity-resolve --domain bank --compact "acme"'
    )
    assert hints.resolve_key_empty({"candidates": [1]}, ["bank"], "acme", 5) is None


def test_text2cypher_empty():
    assert hints.text2cypher_empty({"rows_total": 0}, ["bank"], "q?") == (
        'artmind query graph text2cypher --domain bank --dryRun --compact "q?"; '
        "artmind query orient --domain bank --compact"
    )
    assert hints.text2cypher_empty({"rows_total": 2}, ["bank"], "q") is None
    assert hints.text2cypher_empty({"rows_total": 0, "dry_run": True}, ["bank"], "q") is None


def test_text2sql_empty():
    assert hints.text2sql_empty({"rows": []}, ["bank"], "q?") == (
        'artmind query text2sql --domain bank --dryRun --compact "q?"; '
        "artmind db schema --domain bank --compact"
    )
    assert hints.text2sql_empty({"rows": [{"a": 1}]}, ["bank"], "q") is None


def test_text2_failure():
    assert hints.text2_failure("text2sql", ["bank"], "q", False) == (
        '\nnext: artmind query text2sql --domain bank --dryRun --compact "q"'
    )
    assert hints.text2_failure("text2cypher", ["bank"], "q", False) == (
        '\nnext: artmind query graph text2cypher --domain bank --dryRun --compact "q"'
    )
    assert hints.text2_failure("text2sql", ["bank"], "q", True) == (
        "\nnext: artmind query orient --domain bank --compact"
    )
