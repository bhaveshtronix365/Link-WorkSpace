"""Add task_id to tickets and create task_activities table

Revision ID: 021_task_activities_and_ticket_task_id
Revises: 020_add_discussion_to_attachments
Create Date: 2026-11-22
"""
from alembic import op, context
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "021_task_activities_and_ticket_task_id"
down_revision = "020_add_discussion_to_attachments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind) if not context.is_offline_mode() else None

    # -----------------------------------------------------------------------
    # 1. tickets: add nullable task_id (FK to tasks, ON DELETE SET NULL)
    #    with an index on task_id.
    # -----------------------------------------------------------------------
    if inspector and inspector.has_table("tickets"):
        existing_cols = {c["name"] for c in inspector.get_columns("tickets")}
        if "task_id" not in existing_cols:
            with op.batch_alter_table("tickets") as batch_op:
                batch_op.add_column(
                    sa.Column(
                        "task_id",
                        sa.String(length=36),
                        sa.ForeignKey("tasks.id", ondelete="SET NULL"),
                        nullable=True,
                    )
                )
        existing_indexes = {ix["name"] for ix in inspector.get_indexes("tickets")}
        if "ix_tickets_task_id" not in existing_indexes:
            op.create_index(
                "ix_tickets_task_id",
                "tickets",
                ["task_id"],
            )
    elif not inspector:
        with op.batch_alter_table("tickets") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "task_id",
                    sa.String(length=36),
                    sa.ForeignKey("tasks.id", ondelete="SET NULL"),
                    nullable=True,
                )
            )
        op.create_index(
            "ix_tickets_task_id",
            "tickets",
            ["task_id"],
        )

    # -----------------------------------------------------------------------
    # 2. New table task_activities:
    #    id, task_id (FK, CASCADE), organization_id,
    #    actor_id (FK to users, nullable, SET NULL), action (varchar 40),
    #    field_name (varchar 60, nullable), old_value (text, nullable),
    #    new_value (text, nullable), meta (JSON, nullable), created_at;
    #    indexes on (task_id, created_at) and (organization_id, created_at).
    # -----------------------------------------------------------------------
    has_ta = inspector.has_table("task_activities") if inspector else False
    if not has_ta:
        op.create_table(
            "task_activities",
            sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
            sa.Column("task_id", sa.String(length=36), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
            sa.Column("organization_id", sa.String(length=36), nullable=False),
            sa.Column("actor_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("action", sa.String(length=40), nullable=False),
            sa.Column("field_name", sa.String(length=60), nullable=True),
            sa.Column("old_value", sa.Text(), nullable=True),
            sa.Column("new_value", sa.Text(), nullable=True),
            sa.Column("meta", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index(
            "ix_task_activities_task_id_created_at",
            "task_activities",
            ["task_id", "created_at"],
        )
        op.create_index(
            "ix_task_activities_organization_id_created_at",
            "task_activities",
            ["organization_id", "created_at"],
        )
    elif inspector:
        existing_ta_indexes = {ix["name"] for ix in inspector.get_indexes("task_activities")}
        if "ix_task_activities_task_id_created_at" not in existing_ta_indexes:
            op.create_index(
                "ix_task_activities_task_id_created_at",
                "task_activities",
                ["task_id", "created_at"],
            )
        if "ix_task_activities_organization_id_created_at" not in existing_ta_indexes:
            op.create_index(
                "ix_task_activities_organization_id_created_at",
                "task_activities",
                ["organization_id", "created_at"],
            )

    # -----------------------------------------------------------------------
    # 3. Confirm the tasks table has creator (assigner) and assignee columns:
    #    tasks.assignee_id and tasks.reporter_id exist. Add nothing if they exist.
    # -----------------------------------------------------------------------
    if inspector and inspector.has_table("tasks"):
        task_cols = {c["name"] for c in inspector.get_columns("tasks")}
        # assignee_id and reporter_id already present; no schema changes required.
        pass


def downgrade() -> None:
    pass
