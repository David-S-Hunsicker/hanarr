from sqlalchemy import text

from hanarr.config import Settings
from hanarr.db import make_session_factory


def test_make_session_factory_enables_wal_mode_and_a_long_busy_timeout(tmp_path):
    """Regression test: a background search commits frequently (once per
    matched/rejected posting -- see pipeline.run_search_cycle) while the
    dashboard's own page loads run concurrent reads on separate
    connections from the same pool. SQLite's default rollback-journal mode
    lets a writer's transaction briefly block readers, which under real
    contention (slow disk, antivirus intercepting file I/O, a slower LLM
    stretching out the search) can exceed the default lock wait and
    surface as an unhandled "database is locked" error on whatever page
    was loading. WAL mode is SQLite's standard fix for readers not
    blocking on an in-progress writer; busy_timeout is a second line of
    defense on the write side. See db.py's _set_sqlite_pragmas."""
    settings = Settings(data_dir=tmp_path / "data")
    factory = make_session_factory(settings)

    with factory() as session:
        journal_mode = session.execute(text("PRAGMA journal_mode")).scalar()
        busy_timeout = session.execute(text("PRAGMA busy_timeout")).scalar()

    assert journal_mode == "wal"
    assert busy_timeout == 30000
