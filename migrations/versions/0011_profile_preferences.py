"""Add per-profile search preferences (empty until a profile's Preferences
tab is saved, or the profile was created after this migration -- see
config.py's effective_preferences() for the fallback to shared
config.yaml preferences).

Revision ID: 0011
Revises: 0010
"""
from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing_columns = {col["name"] for col in sa.inspect(bind).get_columns("profiles")}
    if "preferences_json" not in existing_columns:
        op.add_column(
            "profiles", sa.Column("preferences_json", sa.Text(), nullable=False, server_default="")
        )


def downgrade() -> None:
    op.drop_column("profiles", "preferences_json")
