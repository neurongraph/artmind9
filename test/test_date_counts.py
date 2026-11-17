"""Per-document date counts (spec 2026-11-15 §3.4): numbers only, never values."""
from artmind import ingest as ing

SCHEMA = {"entity_types": {"EVENT": {"kind": "occurrent", "properties": {"date_or_time": {"temporal": "valid_from"}}}}}


def _entities(n):
    return [{"id": f"e{i}", "name": f"E{i}", "entity_class": "EVENT", "chunk_id": "c1", "_domain": "pj"}
            for i in range(n)]


def test_counts_dated_ranges_and_unparsed():
    props = [{"id": "e0", "properties": {"date_or_time": "2026-01-07"}},
             {"id": "e1", "properties": {"date_or_time": "2026-01-07 to 2026-01-09"}},
             {"id": "e2", "properties": {"date_or_time": "soonish"}}]
    counts: dict = {}
    ing._build_observations(_entities(3), props, {}, SCHEMA, {"id": "d1", "_valid_from": "2026-01-07"}, counts=counts)
    assert counts == {"dated": 2, "ranges": 1, "unparsed": 1}


def test_counts_start_at_zero_without_dated_facts():
    counts: dict = {"dated": 9}
    ing._build_observations(_entities(1), [], {}, SCHEMA, {"id": "d1"}, counts=counts)
    assert counts == {"dated": 0, "ranges": 0, "unparsed": 0}


def test_the_log_line_carries_no_value():
    assert ing._date_counts_line({"dated": 2, "ranges": 1, "unparsed": 1}) == \
        "dates: 2 dated by property, 1 range, 1 unparsed"
    assert ing._date_counts_line({"dated": 0, "ranges": 2, "unparsed": 0}) == \
        "dates: 0 dated by property, 2 ranges, 0 unparsed"


def test_job_status_shows_the_date_counts(tmp_path, monkeypatch):
    import artmind.db as db
    from artmind import jobs

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "registry.db")
    conn = db._get_db()
    conn.execute(
        "INSERT INTO ingestion_jobs (job_id, status, file_count, processed_count, queued_at) "
        "VALUES ('j1', 'processing', 2, 1, 'now')"
    )
    conn.execute("INSERT INTO ingestion_job_files (job_id, filename, status) VALUES ('j1', 'a.md', 'completed')")
    conn.execute("INSERT INTO ingestion_job_files (job_id, filename, status) VALUES ('j1', 'b.md', 'queued')")
    conn.commit()
    conn.close()

    jobs._update_job_file_status("j1", "a.md", date_counts={"dated": 2, "ranges": 1, "unparsed": 0})

    first, second = jobs._get_job_status("j1")["files"]
    assert first["date_counts"] == {"dated": 2, "ranges": 1, "unparsed": 0}
    assert "date_counts" not in second
    active = jobs._fetch_active_jobs()[0]["files"]
    assert active[0]["date_counts"] == {"dated": 2, "ranges": 1, "unparsed": 0}
