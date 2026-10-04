"""Add outreach-email drafting fields to job_postings.

Revision ID: 0019
Revises: 0018
"""
from alembic import op
import sqlalchemy as sa

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing_columns = {col["name"] for col in sa.inspect(bind).get_columns("job_postings")}
    if "outreach_contact" not in existing_columns:
        op.add_column(
            "job_postings",
            sa.Column("outreach_contact", sa.String(), nullable=False, server_default=""),
        )
    if "outreach_email" not in existing_columns:
        op.add_column(
            "job_postings",
            sa.Column("outreach_email", sa.Text(), nullable=False, server_default=""),
        )
    if "outreach_email_source" not in existing_columns:
        op.add_column(
            "job_postings",
            sa.Column("outreach_email_source", sa.String(), nullable=False, server_default=""),
        )
    if "outreach_email_generated_at" not in existing_columns:
        op.add_column(
            "job_postings", sa.Column("outreach_email_generated_at", sa.DateTime(), nullable=True)
        )


def downgrade() -> None:
    op.drop_column("job_postings", "outreach_email_generated_at")
    op.drop_column("job_postings", "outreach_email_source")
    op.drop_column("job_postings", "outreach_email")
    op.drop_column("job_postings", "outreach_contact")
