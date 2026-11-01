from artmind import sync_state


def _reader(files):
    return lambda name: files.get(name)


BASE = {"document.json": b"{}", "chunks.json": b"[]", "observations.json": b"[]", "relationships.json": b"[]"}


def test_a_folder_without_summaries_keeps_its_old_fingerprint():
    import hashlib

    digest = hashlib.sha256()
    for name in ("document.json", "chunks.json", "observations.json", "relationships.json"):
        digest.update(name.encode() + b"\0" + str(len(BASE[name])).encode() + b"\0" + BASE[name])
    assert sync_state.staging_fingerprint(_reader(BASE)) == digest.hexdigest()


def test_summaries_change_the_fingerprint_when_present():
    without = sync_state.staging_fingerprint(_reader(BASE))
    one = sync_state.staging_fingerprint(_reader({**BASE, "summaries.json": b'{"a": 1}'}))
    two = sync_state.staging_fingerprint(_reader({**BASE, "summaries.json": b'{"a": 2}'}))
    assert len({without, one, two}) == 3


def test_summaries_are_listed_for_vault_sync():
    assert "summaries.json" in sync_state.FINGERPRINT_SOURCES
