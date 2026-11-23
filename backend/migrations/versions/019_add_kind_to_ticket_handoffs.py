"""Add kind column to ticket_handoffs

Revision ID: 019_add_kind_to_ticket_handoffs
Revises: 018_ticket_handoffs_and_attachments
Create Date: 2026-11-22
"""
from alembic import op, context
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "019_add_kind_to_ticket_handoffs"
down_revision = "018_ticket_handoffs_and_attachments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind) if not context.is_offline_mode() else None

    if inspector and inspector.has_table("ticket_handoffs"):
        existing_cols = {c["name"] for c in inspector.get_columns("ticket_handoffs")}
        if "kind" not in existing_cols:
            with op.batch_alter_table("ticket_handoffs") as batch_op:
                batch_op.add_column(
                    sa.Column(
                        "kind",
                        sa.String(length=20),
                        nullable=False,
                        server_default="pass",
                    )
                )
    elif not inspector:
        with op.batch_alter_table("ticket_handoffs") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "kind",
                    sa.String(length=20),
                    nullable=False,
                    server_default="pass",
                )
            )


def downgrade() -> None:
    pass
