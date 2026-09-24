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
    legacy_tables = [
        table
        for table in Base.metadata.sorted_tables
        if table.name != "score_snapshots"
    ]
    Base.metadata.create_all(op.get_bind(), tables=legacy_tables, checkfirst=True)


def downgrade() -> None:
    for table in reversed(Base.metadata.sorted_tables):
        if table.name != "score_snapshots":
            table.drop(op.get_bind(), checkfirst=True)
