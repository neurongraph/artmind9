def test_create_job_persists_stage_only(tmp_path, monkeypatch):
    import sqlite3
    import artmind.db as db
    import artmind.jobs as jobs

    dbfile = tmp_path / "reg.db"
    monkeypatch.setattr(db, "DB_PATH", dbfile)

    job_id = jobs._create_job(["/a.pdf"], domain="d", stage_only=True)

    conn = sqlite3.connect(dbfile)
    row = conn.execute("SELECT stage_only FROM ingestion_jobs WHERE job_id=?", (job_id,)).fetchone()
    assert row[0] == 1
