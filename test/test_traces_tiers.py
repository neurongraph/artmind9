from artmind.traces import tiers


def test_inline_and_listed_items():
    t = tiers.parse("Must: a; b\nOptional: c\nMust-not: d\n- e\nNotes: be strict\nalso this")
    assert t.must == ("a", "b") and t.optional == ("c",) and t.must_not == ("d", "e")
    assert t.notes == "be strict\nalso this" and t.tiered


def test_spellings():
    t = tiers.parse("**Must:** x\nmust not: y\nMUST_NOT: z")
    assert t.must == ("x",) and t.must_not == ("y", "z")


def test_untiered_text_is_notes_only():
    t = tiers.parse("expect the 2024 figure")
    assert not t.tiered and t.notes == "expect the 2024 figure"
    assert not tiers.parse(None).tiered


def test_a_heading_needs_a_colon():
    t = tiers.parse("must have a figure\nnotes about it")
    assert not t.tiered and t.notes == "must have a figure\nnotes about it"
