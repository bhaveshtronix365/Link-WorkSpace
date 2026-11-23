"""Add ticket handoffs, ticket attachment fields and ticket version

Revision ID: 018_ticket_handoffs_and_attachments
Revises: 017_add_organization_industry_other
Create Date: 2026-11-22
"""
from alembic import op, context
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "018_ticket_handoffs_and_attachments"
down_revision = "017_add_organization_industry_other"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind) if not context.is_offline_mode() else None

    # -----------------------------------------------------------------------
    # 1. ticket_attachments:
    # add nullable comment_id (FK to ticket_comments, ON DELETE SET NULL)
    # and any missing: uploader_id, original_name, stored_name, content_type,
    # size_bytes, created_at.
    # Add an index on (ticket_id, created_at).
    # -----------------------------------------------------------------------
    if inspector and inspector.has_table("ticket_attachments"):
        existing_cols = {c["name"] for c in inspector.get_columns("ticket_attachments")}
        with op.batch_alter_table("ticket_attachments") as batch_op:
            if "comment_id" not in existing_cols:
                batch_op.add_column(
                    sa.Column(
                        "comment_id",
                        sa.String(length=36),
                        sa.ForeignKey("ticket_comments.id", ondelete="SET NULL"),
                        nullable=True,
                    )
                )
            if "uploader_id" not in existing_cols:
                batch_op.add_column(
                    sa.Column(
                        "uploader_id",
                        sa.String(length=36),
                        sa.ForeignKey("users.id"),
                        nullable=False,
                    )
                )
            if "original_name" not in existing_cols:
                batch_op.add_column(
                    sa.Column(
                        "original_name",
                        sa.String(length=255),
                        nullable=True,
                    )
                )
            if "stored_name" not in existing_cols:
                batch_op.add_column(
                    sa.Column(
                        "stored_name",
                        sa.String(length=255),
                        nullable=True,
                    )
                )
            if "content_type" not in existing_cols:
                batch_op.add_column(
                    sa.Column(
                        "content_type",
                        sa.String(length=100),
                        nullable=True,
                    )
                )
            if "size_bytes" not in existing_cols:
                batch_op.add_column(
                    sa.Column(
                        "size_bytes",
                        sa.Integer(),
                        nullable=False,
                        server_default="0",
                    )
                )
            if "created_at" not in existing_cols:
                batch_op.add_column(
                    sa.Column(
                        "created_at",
                        sa.DateTime(timezone=True),
                        nullable=False,
                        server_default=sa.func.now(),
                    )
                )

        existing_indexes = {ix["name"] for ix in inspector.get_indexes("ticket_attachments")}
        if "ix_ticket_attachments_ticket_id_created_at" not in existing_indexes:
            op.create_index(
                "ix_ticket_attachments_ticket_id_created_at",
                "ticket_attachments",
                ["ticket_id", "created_at"],
            )
    elif not inspector:
        with op.batch_alter_table("ticket_attachments") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "comment_id",
                    sa.String(length=36),
                    sa.ForeignKey("ticket_comments.id", ondelete="SET NULL"),
                    nullable=True,
                )
            )
            batch_op.add_column(
                sa.Column(
                    "original_name",
                    sa.String(length=255),
                    nullable=True,
                )
            )
            batch_op.add_column(
                sa.Column(
                    "stored_name",
                    sa.String(length=255),
                    nullable=True,
                )
            )
            batch_op.add_column(
                sa.Column(
                    "content_type",
                    sa.String(length=100),
                    nullable=True,
                )
            )
            batch_op.add_column(
                sa.Column(
                    "size_bytes",
                    sa.Integer(),
                    nullable=False,
                    server_default="0",
                )
            )
        op.create_index(
            "ix_ticket_attachments_ticket_id_created_at",
            "ticket_attachments",
            ["ticket_id", "created_at"],
        )

    # -----------------------------------------------------------------------
    # 2. New table ticket_handoffs:
    # id, ticket_id (FK, CASCADE), from_user_id, to_user_id (FKs to users),
    # message (text), status_before, status_after (varchar), created_at,
    # index on (ticket_id, created_at).
    # -----------------------------------------------------------------------
    has_handoffs = inspector.has_table("ticket_handoffs") if inspector else False
    if not has_handoffs:
        op.create_table(
            "ticket_handoffs",
            sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
            sa.Column("ticket_id", sa.String(length=36), sa.ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False),
            sa.Column("from_user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("to_user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("message", sa.Text(), nullable=True),
            sa.Column("status_before", sa.String(length=50), nullable=True),
            sa.Column("status_after", sa.String(length=50), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index(
            "ix_ticket_handoffs_ticket_id_created_at",
            "ticket_handoffs",
            ["ticket_id", "created_at"],
        )
    elif inspector:
        existing_handoff_indexes = {ix["name"] for ix in inspector.get_indexes("ticket_handoffs")}
        if "ix_ticket_handoffs_ticket_id_created_at" not in existing_handoff_indexes:
            op.create_index(
                "ix_ticket_handoffs_ticket_id_created_at",
                "ticket_handoffs",
                ["ticket_id", "created_at"],
            )

    # -----------------------------------------------------------------------
    # 3. Tickets:
    # inspect how status is stored. If it is a varchar, add nothing.
    # If it is a database enum, add the value "passed" safely.
    # Also add a nullable integer version column (default 0) for optimistic locking if it does not exist.
    # -----------------------------------------------------------------------
    if inspector and inspector.has_table("tickets"):
        ticket_cols = inspector.get_columns("tickets")
        status_col = next((c for c in ticket_cols if c["name"] == "status"), None)
        if status_col:
            col_type = status_col["type"]
            type_name = getattr(col_type, "name", None) or type(col_type).__name__
            if isinstance(col_type, sa.Enum) or "ENUM" in str(type_name).upper():
                enum_name = getattr(col_type, "name", None) or "ticketstatus"
                op.execute(sa.text(f"ALTER TYPE {enum_name} ADD VALUE IF NOT EXISTS 'passed'"))

        col_names = {c["name"] for c in ticket_cols}
        if "version" not in col_names:
            with op.batch_alter_table("tickets") as batch_op:
                batch_op.add_column(
                    sa.Column(
                        "version",
                        sa.Integer(),
                        nullable=True,
                        server_default="0",
                    )
                )
    elif not inspector:
        with op.batch_alter_table("tickets") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "version",
                    sa.Integer(),
                    nullable=True,
                    server_default="0",
                )
            )


def downgrade() -> None:
    pass
