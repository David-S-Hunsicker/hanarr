"""Create the compatibility schema used before Hannar migrations.

Revision ID: 0001
Revises:
"""
from alembic import op

from jobcopilot.models import Base

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    legacy_names = {"profiles", "job_postings", "seen_postings", "reminders"}
    legacy_tables = [table for table in Base.metadata.sorted_tables if table.name in legacy_names]
    Base.metadata.create_all(op.get_bind(), tables=legacy_tables, checkfirst=True)


def downgrade() -> None:
    legacy_names = {"profiles", "job_postings", "seen_postings", "reminders"}
    for table in reversed(Base.metadata.sorted_tables):
        if table.name in legacy_names:
            table.drop(op.get_bind(), checkfirst=True)
