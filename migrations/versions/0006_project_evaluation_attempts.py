"""Persist structured evaluator attempts and feedback.

Revision ID: 0006
Revises: 0005
"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("project_evaluations", sa.Column("attempt_number", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("project_evaluations", sa.Column("outcome", sa.String(), nullable=False, server_default="needs_improvement"))
    op.add_column("project_evaluations", sa.Column("scores_json", sa.Text(), nullable=False, server_default="{}"))
    op.add_column("project_evaluations", sa.Column("strengths_json", sa.Text(), nullable=False, server_default="[]"))
    op.add_column("project_evaluations", sa.Column("improvements_json", sa.Text(), nullable=False, server_default="[]"))
    op.add_column("project_evaluations", sa.Column("actionable_feedback_json", sa.Text(), nullable=False, server_default="[]"))
    op.add_column("project_evaluations", sa.Column("error", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("project_evaluations", "error")
    op.drop_column("project_evaluations", "actionable_feedback_json")
    op.drop_column("project_evaluations", "improvements_json")
    op.drop_column("project_evaluations", "strengths_json")
    op.drop_column("project_evaluations", "scores_json")
    op.drop_column("project_evaluations", "outcome")
    op.drop_column("project_evaluations", "attempt_number")
