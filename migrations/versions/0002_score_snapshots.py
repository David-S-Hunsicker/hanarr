"""Add immutable score history and backfill existing matches.

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "score_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("profile_id", sa.Integer(), sa.ForeignKey("profiles.id"), nullable=False),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("job_postings.id"), nullable=False),
        sa.Column("fit_score", sa.Float(), nullable=False),
        sa.Column("fit_rationale", sa.Text(), nullable=False, server_default=""),
        sa.Column("trigger", sa.String(), nullable=False, server_default="initial"),
        sa.Column("scorer_metadata_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.get_bind().execute(
        sa.text(
            "INSERT INTO score_snapshots "
            "(profile_id, job_id, fit_score, fit_rationale, trigger, scorer_metadata_json, created_at) "
            "SELECT profile_id, id, fit_score, fit_rationale, 'initial', "
            "'{\"legacy\": true}', COALESCE(fetched_at, CURRENT_TIMESTAMP) "
            "FROM job_postings WHERE fit_score IS NOT NULL"
        )
    )


def downgrade() -> None:
    op.drop_table("score_snapshots")
