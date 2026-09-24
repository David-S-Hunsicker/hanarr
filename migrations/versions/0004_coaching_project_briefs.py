"""Persist generated coaching briefs, tasks, and affected jobs.

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("brief_json", sa.Text(), nullable=False, server_default="{}"))
    op.create_table(
        "project_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("job_postings.id"), nullable=False),
        sa.UniqueConstraint("project_id", "job_id", name="uq_project_job"),
    )
    op.create_table(
        "project_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(), nullable=False, server_default="todo"),
    )


def downgrade() -> None:
    op.drop_table("project_tasks")
    op.drop_table("project_jobs")
    op.drop_column("projects", "brief_json")
