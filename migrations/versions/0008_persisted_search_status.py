"""Persist last-search time/count/trigger so it survives a restart.

Revision ID: 0008
Revises: 0007
"""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # profiles is one of 0001's "frozen" legacy tables, but 0001 actually
    # creates it by reflecting the *current* Base.metadata (see
    # 0001_legacy_schema.py) restricted to the legacy table names -- so a
    # brand-new database created today already has these columns from 0001
    # itself. Guard with an existence check (matching 0001's own
    # checkfirst=True and 0007's own precedent), rather than assuming an
    # upgrading database, which genuinely predates these columns, is the
    # only case that runs this.
    bind = op.get_bind()
    existing_columns = {col["name"] for col in sa.inspect(bind).get_columns("profiles")}
    if "last_search_at" not in existing_columns:
        op.add_column("profiles", sa.Column("last_search_at", sa.DateTime(), nullable=True))
    if "last_search_new_count" not in existing_columns:
        op.add_column("profiles", sa.Column("last_search_new_count", sa.Integer(), nullable=True))
    if "last_search_trigger" not in existing_columns:
        op.add_column("profiles", sa.Column("last_search_trigger", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("profiles", "last_search_trigger")
    op.drop_column("profiles", "last_search_new_count")
    op.drop_column("profiles", "last_search_at")
