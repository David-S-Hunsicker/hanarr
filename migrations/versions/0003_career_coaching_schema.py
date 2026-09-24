"""Add normalized skills, coaching projects, evidence, and resume proposals.

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def _common_columns():
    return [
        sa.Column("id", sa.Integer(), primary_key=True),
    ]


def upgrade() -> None:
    op.create_table(
        "skills",
        *_common_columns(),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("slug", name="uq_skill_slug"),
    )
    op.create_table(
        "profile_skills",
        *_common_columns(),
        sa.Column("profile_id", sa.Integer(), sa.ForeignKey("profiles.id"), nullable=False),
        sa.Column("skill_id", sa.Integer(), sa.ForeignKey("skills.id"), nullable=False),
        sa.Column("proficiency", sa.Float()),
        sa.Column("evidence", sa.Text(), nullable=False, server_default=""),
        sa.Column("confidence", sa.Float()),
        sa.Column("source", sa.String(), nullable=False, server_default="manual"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("profile_id", "skill_id", name="uq_profile_skill"),
    )
    op.create_table(
        "job_skills",
        *_common_columns(),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("job_postings.id"), nullable=False),
        sa.Column("skill_id", sa.Integer(), sa.ForeignKey("skills.id"), nullable=False),
        sa.Column("requirement", sa.String(), nullable=False, server_default="required"),
        sa.Column("gap_status", sa.String(), nullable=False, server_default="unknown"),
        sa.Column("evidence", sa.Text(), nullable=False, server_default=""),
        sa.Column("rationale", sa.Text(), nullable=False, server_default=""),
        sa.Column("confidence", sa.Float()),
        sa.Column("analyzed_at", sa.DateTime()),
        sa.UniqueConstraint("job_id", "skill_id", name="uq_job_skill"),
    )
    op.create_table(
        "projects",
        *_common_columns(),
        sa.Column("profile_id", sa.Integer(), sa.ForeignKey("profiles.id"), nullable=False),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("job_postings.id")),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("mode", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="planned"),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("target_outcome", sa.Text(), nullable=False, server_default=""),
        sa.Column("opted_in_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime()),
    )
    op.create_table(
        "project_skills",
        *_common_columns(),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("skill_id", sa.Integer(), sa.ForeignKey("skills.id"), nullable=False),
        sa.Column("target_level", sa.Float()),
        sa.Column("evidence", sa.Text(), nullable=False, server_default=""),
        sa.UniqueConstraint("project_id", "skill_id", name="uq_project_skill"),
    )
    op.create_table(
        "project_submissions",
        *_common_columns(),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(), nullable=False, server_default="draft"),
        sa.Column("submitted_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "project_evaluations",
        *_common_columns(),
        sa.Column("submission_id", sa.Integer(), sa.ForeignKey("project_submissions.id"), nullable=False),
        sa.Column("evaluator", sa.String(), nullable=False, server_default="manual"),
        sa.Column("passed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("score", sa.Float()),
        sa.Column("feedback", sa.Text(), nullable=False, server_default=""),
        sa.Column("evaluated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "proven_skills",
        *_common_columns(),
        sa.Column("profile_id", sa.Integer(), sa.ForeignKey("profiles.id"), nullable=False),
        sa.Column("skill_id", sa.Integer(), sa.ForeignKey("skills.id"), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id")),
        sa.Column("evaluation_id", sa.Integer(), sa.ForeignKey("project_evaluations.id")),
        sa.Column("evidence", sa.Text(), nullable=False, server_default=""),
        sa.Column("proven_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("profile_id", "skill_id", name="uq_proven_profile_skill"),
    )
    op.create_table(
        "resume_versions",
        *_common_columns(),
        sa.Column("profile_id", sa.Integer(), sa.ForeignKey("profiles.id"), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "resume_proposals",
        *_common_columns(),
        sa.Column("profile_id", sa.Integer(), sa.ForeignKey("profiles.id"), nullable=False),
        sa.Column("base_version_id", sa.Integer(), sa.ForeignKey("resume_versions.id")),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id")),
        sa.Column("proposed_content", sa.Text(), nullable=False),
        sa.Column("diff", sa.Text(), nullable=False, server_default=""),
        sa.Column("rationale", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("decided_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    for table in (
        "resume_proposals",
        "resume_versions",
        "proven_skills",
        "project_evaluations",
        "project_submissions",
        "project_skills",
        "projects",
        "job_skills",
        "profile_skills",
        "skills",
    ):
        op.drop_table(table)
