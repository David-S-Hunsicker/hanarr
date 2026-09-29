"""Add star_questions/star_stories for the STAR behavioral-story builder --
generated candidate questions grouped by competency, and a per-profile story
bank so a story is written once and revisited/re-practiced instead of
rebuilt from scratch every job search cycle. See models.py's StarQuestion
and StarStory, and star_stories.py.

Revision ID: 0016
Revises: 0015
"""
from alembic import op
import sqlalchemy as sa

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing_tables = set(sa.inspect(bind).get_table_names())
    if "star_questions" not in existing_tables:
        op.create_table(
            "star_questions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("profile_id", sa.Integer(), sa.ForeignKey("profiles.id"), nullable=False),
            sa.Column("question", sa.Text(), nullable=False),
            sa.Column("competency", sa.String(), nullable=False, server_default=""),
            sa.Column("source", sa.String(), nullable=False, server_default="generic"),
            sa.Column("job_id", sa.Integer(), sa.ForeignKey("job_postings.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
    if "star_stories" not in existing_tables:
        op.create_table(
            "star_stories",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("question_id", sa.Integer(), sa.ForeignKey("star_questions.id"), nullable=False),
            sa.Column("situation", sa.Text(), nullable=False, server_default=""),
            sa.Column("task", sa.Text(), nullable=False, server_default=""),
            sa.Column("action", sa.Text(), nullable=False, server_default=""),
            sa.Column("result", sa.Text(), nullable=False, server_default=""),
            sa.Column("feedback", sa.Text(), nullable=False, server_default=""),
            sa.Column("tightened_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("evaluator", sa.String(), nullable=False, server_default="pending"),
            sa.Column("status", sa.String(), nullable=False, server_default="draft"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("question_id", name="uq_star_story_question"),
        )


def downgrade() -> None:
    op.drop_table("star_stories")
    op.drop_table("star_questions")
