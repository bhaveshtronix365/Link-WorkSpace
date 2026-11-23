"""Add discussion link to ticket_attachments

Revision ID: 020_add_discussion_to_attachments
Revises: 019_add_kind_to_ticket_handoffs
Create Date: 2026-11-22
"""
from alembic import op, context
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "020_add_discussion_to_attachments"
down_revision = "019_add_kind_to_ticket_handoffs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind) if not context.is_offline_mode() else None

    if inspector and inspector.has_table("ticket_attachments"):
        existing_cols = {c["name"] for c in inspector.get_columns("ticket_attachments")}
        with op.batch_alter_table("ticket_attachments") as batch_op:
            batch_op.alter_column("ticket_id", existing_type=sa.String(length=36), nullable=True)
            if "discussion_id" not in existing_cols:
                batch_op.add_column(
                    sa.Column(
                        "discussion_id",
                        sa.String(length=36),
                        sa.ForeignKey("discussions.id", ondelete="CASCADE"),
                        nullable=True,
                    )
                )
            if "discussion_comment_id" not in existing_cols:
                batch_op.add_column(
                    sa.Column(
                        "discussion_comment_id",
                        sa.String(length=36),
                        sa.ForeignKey("discussion_comments.id", ondelete="SET NULL"),
                        nullable=True,
                    )
                )

        existing_indexes = {ix["name"] for ix in inspector.get_indexes("ticket_attachments")}
        if "ix_ticket_attachments_discussion_id_created_at" not in existing_indexes:
            op.create_index(
                "ix_ticket_attachments_discussion_id_created_at",
                "ticket_attachments",
                ["discussion_id", "created_at"],
            )
    elif not inspector:
        with op.batch_alter_table("ticket_attachments") as batch_op:
            batch_op.alter_column("ticket_id", existing_type=sa.String(length=36), nullable=True)
            batch_op.add_column(
                sa.Column(
                    "discussion_id",
                    sa.String(length=36),
                    sa.ForeignKey("discussions.id", ondelete="CASCADE"),
                    nullable=True,
                )
            )
            batch_op.add_column(
                sa.Column(
                    "discussion_comment_id",
                    sa.String(length=36),
                    sa.ForeignKey("discussion_comments.id", ondelete="SET NULL"),
                    nullable=True,
                )
            )
        op.create_index(
            "ix_ticket_attachments_discussion_id_created_at",
            "ticket_attachments",
            ["discussion_id", "created_at"],
        )


def downgrade() -> None:
    pass
