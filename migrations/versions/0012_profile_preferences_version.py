"""Add a per-profile preferences version counter, separate from
config.yaml's own optimistic-concurrency version -- see models.py's
Profile.preferences_version.

Revision ID: 0012
Revises: 0011
"""
from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing_columns = {col["name"] for col in sa.inspect(bind).get_columns("profiles")}
    if "preferences_version" not in existing_columns:
        op.add_column(
            "profiles", sa.Column("preferences_version", sa.Integer(), nullable=False, server_default="0")
        )


def downgrade() -> None:
    op.drop_column("profiles", "preferences_version")
