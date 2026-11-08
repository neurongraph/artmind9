"""`_init_db` migrations are safe when several processes open an older registry at once.

The Obsidian plugin runs several `artmind` commands in parallel; each calls
`_init_db`, which reads `PRAGMA table_info` and then `ALTER TABLE ... ADD
COLUMN`. Without the write lock held across both, two callers saw the column
missing and the second failed with "duplicate column name: contract".
"""

import sqlite3
import threading

from artmind import db


def test_concurrent_init_on_an_older_registry_adds_each_column_once(tmp_path, monkeypatch):
    path = tmp_path / "registry.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    db._init_db()
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE trace_turns DROP COLUMN contract")
    conn.execute("ALTER TABLE benchmark_runs DROP COLUMN contract")
    conn.commit()
    conn.close()

    errors: list[BaseException] = []
    barrier = threading.Barrier(8)

    def open_registry():
        barrier.wait()
        try:
            db._init_db()
        except BaseException as exc:  # noqa: BLE001 -- collected for the assert
            errors.append(exc)

    threads = [threading.Thread(target=open_registry) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    conn = sqlite3.connect(path)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(trace_turns)")]
    conn.close()
    assert cols.count("contract") == 1
