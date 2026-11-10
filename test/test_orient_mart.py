from test_orient_card import FACTS, _cat

from artmind.orient import card


def test_mart_tables_fold_into_one_line():
    here = [{"table": "complaints", "row_count": 20}, {"table": "mart_fact_happening", "row_count": 120},
            {"table": "mart_fact_mention", "row_count": 300}, {"table": "mart_whole_trip", "row_count": 9},
            {"table": "mart_dim_place", "row_count": 50}]
    c = card.build_card(_cat(), FACTS, narrator=None, family=("bank", 1), tables_here=here)
    assert c["mart"] == {"facts": 120, "mentions": 300, "wholes": {"trip": 9}}
    assert [t["table"] for t in c["tables"]] == ["complaints"]


def test_no_mart_no_key():
    c = card.build_card(_cat(), FACTS, narrator=None, family=("bank", 1), tables_here=[{"table": "t", "row_count": 1}])
    assert "mart" not in c
