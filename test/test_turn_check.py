"""artmind/turn_check.py: open items from a turn's spans (spec 2026-11-11 turn check §3). Synthetic spans only."""

from artmind import turn_check as tc


def span(command, rows=1, rows_total=None, truncated=False, args=None, **ev):
    return {"command": command, "rows": rows, "rows_total": rows_total, "truncated": truncated,
            "args": args or {}, "evidence": ev}


def kinds(spans):
    return [i["kind"] for i in tc.open_items(spans)]


def test_next_command_paths():
    assert tc.next_command("artmind query graph pattern5 --domain d --compact") == ["query graph pattern5", "query graph"]
    assert tc.next_command("artmind query survey --shape brief 'q'") == ["query survey"]
    assert tc.next_command("add --includeHistory: 1 retired document") == []
    assert tc.next_command(None) == []


def test_a_followed_next_is_closed_and_an_unfollowed_one_is_open():
    s = span("query vector-text", next="artmind query survey --domain d 'q'")
    assert kinds([s, span("query survey")]) == []
    assert kinds([s, span("query chunks")]) == ["next_unfollowed"]


def test_partial_closed_by_page_all_or_its_next():
    cut = span("query graph pattern7", rows=10, rows_total=40)
    assert kinds([cut]) == ["partial_relied_on"]
    assert kinds([cut, span("query page")]) == []
    assert kinds([cut, span("query graph pattern7", args={"all": True})]) == []
    cut_next = span("query survey", truncated=True, next="artmind query page h1 --from 10")
    assert kinds([cut_next, span("query page")]) == []
    assert kinds([cut_next]) == ["next_unfollowed", "partial_relied_on"]


def test_mart_rows_need_a_document_check():
    mart = span("query text2sql", rows=3, sql="select * from d__mart_fact_happening")
    assert kinds([mart]) == ["mart_unchecked"]
    assert kinds([mart, span("query survey")]) == []
    assert kinds([span("query text2sql", rows=3, sql="select * from d__people")]) == []
    assert kinds([span("query text2sql", rows=0, sql="select * from d__mart_x")]) == []


def test_unread_part_but_not_health_not_cached():
    assert kinds([span("query orient", warnings=["d: classes not read"])]) == ["unread_part"]
    assert kinds([span("query orient", warnings=["coverage not computed for 2 domains: health not cached"])]) == []


def test_identity_open_until_an_id_is_used():
    twins = span("query entity-resolve", warnings=["2 entities share the name 'x' (Person): treat them as one"])
    assert kinds([twins, span("query entity-context")]) == ["identity_open"]
    assert kinds([twins, span("query entity-context", args={"entity_id": ["e1"]})]) == []


def test_odd_spans_never_raise():
    assert tc.open_items([None, {"command": None}, {"command": "query x", "evidence": "nope", "args": []}]) == []
