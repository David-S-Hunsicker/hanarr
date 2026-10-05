"""Add fit_score_method to job_postings, so the dashboard can show whether a
job was scored by the LLM or the rule-based keyword fallback.

Revision ID: 0020
Revises: 0019
"""
from alembic import op
import sqlalchemy as sa

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing_columns = {col["name"] for col in sa.inspect(bind).get_columns("job_postings")}
    if "fit_score_method" not in existing_columns:
        op.add_column(
            "job_postings", sa.Column("fit_score_method", sa.String(), nullable=True)
        )


def downgrade() -> None:
    op.drop_column("job_postings", "fit_score_method")
