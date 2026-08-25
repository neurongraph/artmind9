"""The key function — the pure normalization the whole projection aggregates on.

A table test, deliberately. Every row is a real shape from the banking corpus
or a near-miss the measurement-tail rule must NOT eat.
"""
import pytest

from artmind.observations import (
    aggregate_key,
    entity_id,
    key_string,
    normalize_name,
    observation_id,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        # ── layers 1-4: unicode, case, whitespace, trailing punctuation ──
        ("SmartSaver Account", "smartsaver account"),
        ("  SmartSaver   Account  ", "smartsaver account"),
        ("SmartSaver Account", "smartsaver account"),          # nbsp
        ("ＳｍａｒｔＳaver Account", "smartsaver account"),            # full-width -> NFKC
        ("SmartSaver Account.", "smartsaver account"),
        ("SmartSaver Account,", "smartsaver account"),
        ("STRASSE", "strasse"),
        ("Straße", "strasse"),                                       # casefold, not lower()
        (None, ""),
        ("", ""),
        # ── layer 5: dash/colon tail, stripped ONLY with a digit ──
        ("SmartSaver Account Tier 2 Rate — 4.60% AER, effective 2026-02-01",
         "smartsaver account tier 2 rate"),
        ("SmartSaver Account Tier 1 Rate - 4.50% AER (£0–£10,000), effective 2026-01-15",
         "smartsaver account tier 1 rate"),
        ("SmartSaver Plus: 5.25% AER", "smartsaver plus"),
        ("Bank of England Base Rate (4.00%)", "bank of england base rate"),
        ("Rate Entry (2026-03-01)", "rate entry"),
        # both a parenthetical and a dash tail
        ("Tier 3 Rate (£50,001+) — 4.80% AER", "tier 3 rate"),
        # ── layer 5: tails WITHOUT a digit are meaning, and are preserved ──
        ("Financial Conduct Authority (FCA)", "financial conduct authority (fca)"),
        ("Overdraft Rate — Arranged", "overdraft rate — arranged"),
        ("Complaints Policy: Retail", "complaints policy: retail"),
        # ── a digit inside the STEM is not a tail ──
        ("SmartSaver Account Tier 2 Rate", "smartsaver account tier 2 rate"),
        ("Basel III Framework", "basel iii framework"),
        # a hyphen with no surrounding whitespace is one token, never a separator
        ("SAV-001", "sav-001"),
        ("Product SAV-001", "product sav-001"),
        ("£10,001-£50,000 Tier", "£10,001-£50,000 tier"),
    ],
)
def test_normalize_name(raw, expected):
    assert normalize_name(raw) == expected


def test_normalize_is_idempotent():
    """Normalizing twice must equal normalizing once — otherwise a rebuild that
    re-reads a stored key could drift away from the one it wrote."""
    for raw in [
        "SmartSaver Account Tier 2 Rate — 4.60% AER, effective 2026-02-01",
        "Financial Conduct Authority (FCA)",
        "Tier 3 Rate (£50,001+) — 4.80% AER",
    ]:
        once = normalize_name(raw)
        assert normalize_name(once) == once


def test_the_three_rate_schedule_names_collapse_to_one_key():
    """The vertical slice, at the key level: three documents naming the same
    tier three different ways must produce ONE aggregate key."""
    january = "SmartSaver Account Tier 2 Rate — 4.70% AER (£10,001–£50,000), effective 2026-01-15"
    february = "SmartSaver Account Tier 2 Rate - 4.60% AER"
    march = "SmartSaver Account Tier 2 Rate"

    keys = {aggregate_key(n, "RATE_ENTRY", "banking.reference") for n in (january, february, march)}
    assert keys == {("smartsaver account tier 2 rate", "RATE_ENTRY", "banking.reference")}


def test_entity_id_is_deterministic_and_key_scoped():
    key = aggregate_key("SmartSaver Account Tier 2 Rate", "RATE_ENTRY", "banking.reference")
    assert entity_id(key) == entity_id(key)
    # the same name in another class or domain is a different entity
    assert entity_id(key) != entity_id(aggregate_key("SmartSaver Account Tier 2 Rate", "PRODUCT", "banking.reference"))
    assert entity_id(key) != entity_id(aggregate_key("SmartSaver Account Tier 2 Rate", "RATE_ENTRY", "banking.policy"))
    # and it is a hash of the key string, not of anything stateful
    import hashlib
    assert entity_id(key) == hashlib.sha256(key_string(key).encode("utf-8")).hexdigest()


def test_entity_id_survives_a_name_that_only_differs_by_its_measurement_tail():
    """Byte-identical ids across a re-ingest that renamed the measurement."""
    a = entity_id(aggregate_key("SmartSaver Account Tier 2 Rate — 4.70% AER", "RATE_ENTRY", "d"))
    b = entity_id(aggregate_key("SmartSaver Account Tier 2 Rate — 4.50% AER", "RATE_ENTRY", "d"))
    assert a == b


def test_observation_id_is_chunk_scoped():
    """One document version asserting the same thing in two chunks yields two
    observations; re-writing the same chunk yields one."""
    args = ("SmartSaver Account Tier 2 Rate", "RATE_ENTRY", "banking.reference")
    first = observation_id("doc_001", *args)
    again = observation_id("doc_001", *args)
    other_chunk = observation_id("doc_002", *args)
    assert first == again
    assert first != other_chunk


def test_observation_id_normalizes_its_name_component():
    """Two chunks wording the same canonical name differently still collide on
    the same observation id when they are the same chunk — the id is built on
    the normalized name, matching the key."""
    a = observation_id("doc_001", "SmartSaver Account Tier 2 Rate", "RATE_ENTRY", "d")
    b = observation_id("doc_001", "  smartsaver   account tier 2 rate ", "RATE_ENTRY", "d")
    assert a == b
