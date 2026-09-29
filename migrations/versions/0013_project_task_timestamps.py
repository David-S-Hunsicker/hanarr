"""Track when a project task's status last changed, so the coaching-project
loop-closure work (stale-project nudges) can tell an abandoned project from
one that's still being actively worked -- see models.py's ProjectTask.updated_at.

Revision ID: 0013
Revises: 0012
"""
from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing_columns = {col["name"] for col in sa.inspect(bind).get_columns("project_tasks")}
    if "updated_at" not in existing_columns:
        op.add_column("project_tasks", sa.Column("updated_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("project_tasks", "updated_at")
