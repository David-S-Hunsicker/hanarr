"""Add dismissed_tutorials -- a narrow (profile_id, tutorial_key) table, one
row per tutorial a profile has dismissed, rather than one column per
tutorial. Adding a new tutorial later needs zero migrations: the app just
starts writing/reading a new tutorial_key value in the same table. The
master on/off switch ("tutorialMode") is a single global behavior flag and
lives in config.yaml with the rest of Settings, not in this per-profile
table. See models.py's DismissedTutorial and tutorials.py.

Revision ID: 0017
Revises: 0016
"""
from alembic import op
import sqlalchemy as sa

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing_tables = set(sa.inspect(bind).get_table_names())
    if "dismissed_tutorials" not in existing_tables:
        op.create_table(
            "dismissed_tutorials",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("profile_id", sa.Integer(), sa.ForeignKey("profiles.id"), nullable=False),
            sa.Column("tutorial_key", sa.String(), nullable=False),
            sa.Column("dismissed_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("profile_id", "tutorial_key", name="uq_dismissed_tutorial"),
        )


def downgrade() -> None:
    op.drop_table("dismissed_tutorials")
