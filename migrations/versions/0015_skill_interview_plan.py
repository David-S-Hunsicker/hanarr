"""Add an explicit ordered improvement plan to a skill interview result, so a
remediate/rebuild verdict gives a concrete next-steps checklist instead of
leaving the person to invent their own improvement plan. See models.py's
SkillInterview.plan_json.

Revision ID: 0015
Revises: 0014
"""
from alembic import op
import sqlalchemy as sa

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing_columns = {col["name"] for col in sa.inspect(bind).get_columns("skill_interviews")}
    if "plan_json" not in existing_columns:
        op.add_column("skill_interviews", sa.Column("plan_json", sa.Text(), nullable=False, server_default="[]"))


def downgrade() -> None:
    op.drop_column("skill_interviews", "plan_json")
