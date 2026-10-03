"""Add chat_messages -- the Coach chatbot's persisted, per-profile
conversation history, so the thread survives a restart like everything
else in this app. action_json/action_status carry a pending action
proposal (see coach_actions.py) until the user confirms or declines it
from the chat UI. See models.py's ChatMessage, coach.py.

Revision ID: 0018
Revises: 0017
"""
from alembic import op
import sqlalchemy as sa

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing_tables = set(sa.inspect(bind).get_table_names())
    if "chat_messages" not in existing_tables:
        op.create_table(
            "chat_messages",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("profile_id", sa.Integer(), sa.ForeignKey("profiles.id"), nullable=False),
            sa.Column("role", sa.String(), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("action_json", sa.Text(), nullable=True),
            sa.Column("action_status", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )


def downgrade() -> None:
    op.drop_table("chat_messages")
