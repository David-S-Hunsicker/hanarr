"""Add skill_interviews -- the mini-interview skill-assessment table, so a
claimed skill can be tested with a short bounded Q&A instead of only trusted
from resume wording or a self-reported number. See models.py's SkillInterview
and skill_interview.py.

Revision ID: 0014
Revises: 0013
"""
from alembic import op
import sqlalchemy as sa

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "skill_interviews" in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        "skill_interviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("profile_id", sa.Integer(), sa.ForeignKey("profiles.id"), nullable=False),
        sa.Column("skill_id", sa.Integer(), sa.ForeignKey("skills.id"), nullable=False),
        sa.Column("questions_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("answers_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("verdict", sa.String(), nullable=True),
        sa.Column("feedback", sa.Text(), nullable=False, server_default=""),
        sa.Column("resources_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("evaluator", sa.String(), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("answered_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("skill_interviews")
