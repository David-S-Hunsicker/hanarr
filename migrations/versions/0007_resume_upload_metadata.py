"""Record the uploaded resume's original filename and last-parsed time.

Revision ID: 0007
Revises: 0006
"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # profiles is one of 0001's "frozen" legacy tables, but 0001 actually
    # creates it by reflecting the *current* Base.metadata (see
    # 0001_legacy_schema.py) restricted to the legacy table names -- so a
    # brand-new database created today already has these columns from 0001
    # itself. Guard with an existence check (matching 0001's own
    # checkfirst=True) rather than assuming an upgrading database, which
    # genuinely predates these columns, is the only case that runs this.
    bind = op.get_bind()
    existing_columns = {col["name"] for col in sa.inspect(bind).get_columns("profiles")}
    if "resume_original_filename" not in existing_columns:
        op.add_column("profiles", sa.Column("resume_original_filename", sa.String(), nullable=True))
    if "resume_parsed_at" not in existing_columns:
        op.add_column("profiles", sa.Column("resume_parsed_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("profiles", "resume_parsed_at")
    op.drop_column("profiles", "resume_original_filename")
