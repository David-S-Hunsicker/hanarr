"""Add an optional label to resume_versions so multiple saved resumes can
be told apart (e.g. "Backend-focused" vs "Data-focused") beyond a bare
version number.

Revision ID: 0009
Revises: 0008
"""
from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing_columns = {col["name"] for col in sa.inspect(bind).get_columns("resume_versions")}
    if "label" not in existing_columns:
        op.add_column("resume_versions", sa.Column("label", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("resume_versions", "label")
