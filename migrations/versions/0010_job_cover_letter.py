"""Add cover-letter drafting fields to job_postings.

Revision ID: 0010
Revises: 0009
"""
from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing_columns = {col["name"] for col in sa.inspect(bind).get_columns("job_postings")}
    if "cover_letter" not in existing_columns:
        op.add_column(
            "job_postings",
            sa.Column("cover_letter", sa.Text(), nullable=False, server_default=""),
        )
    if "cover_letter_source" not in existing_columns:
        op.add_column(
            "job_postings",
            sa.Column("cover_letter_source", sa.String(), nullable=False, server_default=""),
        )
    if "cover_letter_generated_at" not in existing_columns:
        op.add_column(
            "job_postings", sa.Column("cover_letter_generated_at", sa.DateTime(), nullable=True)
        )


def downgrade() -> None:
    op.drop_column("job_postings", "cover_letter_generated_at")
    op.drop_column("job_postings", "cover_letter_source")
    op.drop_column("job_postings", "cover_letter")
