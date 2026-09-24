# Database migrations

Hannar uses [Alembic](https://alembic.sqlalchemy.org/) with SQLite and SQLAlchemy.
`jobcopilot.db.make_session_factory` runs migrations before returning a session factory.

The `0001` revision is the frozen compatibility baseline for the schema that existed
before migrations were introduced. Existing databases are stamped at that revision
without recreating tables, then upgraded through later revisions. New databases run
the baseline normally. Before an upgrade, the application creates a timestamped copy
under `data/backups/`; restore that SQLite file if an upgrade fails.

Migrations are additive and should preserve existing IDs and timestamps. Never edit an
applied revision; add a new revision instead.
