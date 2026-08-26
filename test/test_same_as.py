"""same_as.yaml — load/save round-trip, canonical validation, overlap dedup.

No Neo4j, no LLM: this module is the curated-file boundary only.
"""
from artmind.same_as import load_groups, save_groups, validate_groups


def _k(name, cls, domain):
    return (name, cls, domain)


# ── load_groups: canonical parsing ──────────────────────────────────────────

def test_load_groups_puts_canonical_first(tmp_path):
    path = tmp_path / "same_as.yaml"
    path.write_text("""
groups:
  - canonical: "financial conduct authority|REGULATOR|banking.reference"
    members:
      - "fca|REGULATOR|banking.risk_governance"
      - "financial conduct authority|REGULATOR|banking.reference"
""", encoding="utf-8")
    groups = load_groups(path)
    assert len(groups) == 1
    assert groups[0][0] == ("financial conduct authority", "REGULATOR", "banking.reference")
    assert set(groups[0]) == {
        ("financial conduct authority", "REGULATOR", "banking.reference"),
        ("fca", "REGULATOR", "banking.risk_governance"),
    }


def test_load_groups_skips_group_with_no_canonical(tmp_path):
    path = tmp_path / "same_as.yaml"
    path.write_text("""
groups:
  - members:
      - "a|CLASS|d"
      - "b|CLASS|d"
""", encoding="utf-8")
    assert load_groups(path) == []


def test_load_groups_skips_group_whose_canonical_is_not_a_member(tmp_path):
    path = tmp_path / "same_as.yaml"
    path.write_text("""
groups:
  - canonical: "c|CLASS|d"
    members:
      - "a|CLASS|d"
      - "b|CLASS|d"
""", encoding="utf-8")
    assert load_groups(path) == []


def test_load_groups_skips_single_member_group(tmp_path):
    path = tmp_path / "same_as.yaml"
    path.write_text("""
groups:
  - canonical: "a|CLASS|d"
    members:
      - "a|CLASS|d"
""", encoding="utf-8")
    assert load_groups(path) == []


def test_missing_file_returns_empty(tmp_path):
    assert load_groups(tmp_path / "nope.yaml") == []


def test_malformed_yaml_returns_empty_not_raises(tmp_path):
    path = tmp_path / "same_as.yaml"
    path.write_text("groups: [this is not: valid: yaml:", encoding="utf-8")
    assert load_groups(path) == []


# ── save_groups / round-trip ─────────────────────────────────────────────────

def test_save_then_load_round_trips_canonical_first(tmp_path):
    path = tmp_path / "same_as.yaml"
    group = [
        _k("FCA", "REGULATOR", "banking.reference"),
        _k("Financial Conduct Authority", "REGULATOR", "banking.risk_governance"),
    ]
    save_groups([group], path)
    reloaded = load_groups(path)
    assert reloaded == [group]


def test_save_creates_parent_directory(tmp_path):
    path = tmp_path / "nested" / "dir" / "same_as.yaml"
    save_groups([[_k("a", "C", "d"), _k("b", "C", "d")]], path)
    assert path.exists()


# ── validate_groups: overlap dedup, no closure ──────────────────────────────

def test_validate_drops_whole_group_when_canonical_already_claimed():
    g1 = [_k("a", "C", "d"), _k("b", "C", "d")]
    g2 = [_k("a", "C", "d"), _k("c", "C", "d")]  # a's canonical slot taken by g1
    result = validate_groups([g1, g2])
    assert result == [g1]


def test_validate_drops_only_the_overlapping_member_from_a_later_group():
    g1 = [_k("a", "C", "d"), _k("b", "C", "d")]
    g2 = [_k("z", "C", "d"), _k("b", "C", "d"), _k("y", "C", "d")]  # b already claimed
    result = validate_groups([g1, g2])
    assert result[0] == g1
    assert result[1] == [_k("z", "C", "d"), _k("y", "C", "d")]


def test_validate_drops_group_reduced_to_one_member():
    g1 = [_k("a", "C", "d"), _k("b", "C", "d")]
    g2 = [_k("z", "C", "d"), _k("b", "C", "d")]  # b claimed, z alone isn't a group
    result = validate_groups([g1, g2])
    assert result == [g1]


def test_validate_no_overlap_keeps_both_groups():
    g1 = [_k("a", "C", "d"), _k("b", "C", "d")]
    g2 = [_k("x", "C", "d"), _k("y", "C", "d")]
    assert validate_groups([g1, g2]) == [g1, g2]


def test_load_groups_applies_validation(tmp_path):
    # Two groups both claiming "b" as canonical/member -- the second's
    # canonical collides with the first's non-canonical member. No
    # union-find: the file's own second group simply loses that key.
    path = tmp_path / "same_as.yaml"
    path.write_text("""
groups:
  - canonical: "a|C|d"
    members:
      - "a|C|d"
      - "b|C|d"
  - canonical: "b|C|d"
    members:
      - "b|C|d"
      - "c|C|d"
""", encoding="utf-8")
    groups = load_groups(path)
    assert len(groups) == 1
    assert groups[0][0] == ("a", "C", "d")
