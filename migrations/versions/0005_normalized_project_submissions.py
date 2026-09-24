"""Normalize coaching project submissions and local artifacts.

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "project_submissions",
        sa.Column("kind", sa.String(), nullable=False, server_default="written_response"),
    )
    op.add_column("project_submissions", sa.Column("title", sa.String(), nullable=False, server_default=""))
    op.add_column("project_submissions", sa.Column("artifact_dir", sa.String(), nullable=True))
    op.add_column(
        "project_submissions",
        sa.Column("manifest_json", sa.Text(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "project_submissions",
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("project_submissions", "metadata_json")
    op.drop_column("project_submissions", "manifest_json")
    op.drop_column("project_submissions", "artifact_dir")
    op.drop_column("project_submissions", "title")
    op.drop_column("project_submissions", "kind")
