import uuid
import pytest
from datetime import datetime, timezone
from httpx import AsyncClient, ASGITransport
from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from backend.app.main import app
from backend.app.core.config import settings
from backend.app.database import Base, get_db
from backend.app.dependencies import get_current_active_user, get_current_user_id
from backend.app.models import User, Organization, Task, TaskActivity, TaskComment, TaskAttachment, Ticket, Project

# Direct non-pooled connection for test isolated schema
async_direct_url = settings.DATABASE_URL.replace('-pooler', '')
sync_direct_url = async_direct_url.replace('postgresql+asyncpg://', 'postgresql://').replace('?ssl=require', '?sslmode=require')

current_mock_user: User | None = None

def override_get_current_active_user():
    global current_mock_user
    return current_mock_user

def override_get_current_user_id():
    global current_mock_user
    return current_mock_user.id if current_mock_user else None


@pytest.mark.anyio
async def test_task_activities_lifecycle():
    global current_mock_user

    test_schema = f"test_task_act_{uuid.uuid4().hex[:8]}"

    # Setup isolated schema via sync psycopg2
    import psycopg2
    conn = psycopg2.connect(sync_direct_url)
    cur = conn.cursor()
    cur.execute(f"CREATE SCHEMA IF NOT EXISTS {test_schema};")
    conn.commit()
    conn.close()

    engine = create_async_engine(
        async_direct_url,
        connect_args={"server_settings": {"search_path": test_schema}},
        echo=False,
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    try:
        # Create all tables in isolated schema
        async with engine.begin() as db_conn:
            await db_conn.run_sync(Base.metadata.create_all)

        org1_id = f"org_{uuid.uuid4().hex[:8]}"
        org2_id = f"org_{uuid.uuid4().hex[:8]}"

        owner_user = User(
            id=f"usr_owner_{uuid.uuid4().hex[:6]}",
            email=f"owner_{uuid.uuid4().hex[:4]}@example.com",
            hashed_password="hash",
            full_name="Alice Owner",
            display_name="Alice the Great",
            role="owner",
            organization_id=org1_id,
            is_active=True,
        )
        member_user = User(
            id=f"usr_member_{uuid.uuid4().hex[:6]}",
            email=f"member_{uuid.uuid4().hex[:4]}@example.com",
            hashed_password="hash",
            full_name="Bob Member",
            display_name=None,  # Fallback to full_name
            role="member",
            organization_id=org1_id,
            is_active=True,
        )
        foreign_user = User(
            id=f"usr_foreign_{uuid.uuid4().hex[:6]}",
            email=f"foreign_{uuid.uuid4().hex[:4]}@example.com",
            hashed_password="hash",
            full_name="Foreign Spy",
            role="owner",
            organization_id=org2_id,
            is_active=True,
        )

        prj_key = f"P{uuid.uuid4().hex[:4].upper()}"
        project1 = Project(
            id=f"proj_{uuid.uuid4().hex[:8]}",
            name="Alpha Project",
            key=prj_key,
            organization_id=org1_id,
        )

        ticket1 = Ticket(
            id=f"tick_{uuid.uuid4().hex[:8]}",
            ticket_number=f"TCK-{uuid.uuid4().hex[:6].upper()}",
            title="Database Latency Spike",
            project_id=project1.id,
            severity="critical",
            status="open",
            reporter_id=owner_user.id,
        )

        org1 = Organization(id=org1_id, name="Alpha Org", slug=f"alpha-{uuid.uuid4().hex[:6]}")
        org2 = Organization(id=org2_id, name="Beta Org", slug=f"beta-{uuid.uuid4().hex[:6]}")

        async with session_factory() as s:
            s.add_all([org1, org2, owner_user, member_user, foreign_user, project1, ticket1])
            await s.commit()

        async def override_get_db():
            async with session_factory() as s:
                yield s

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_current_active_user] = override_get_current_active_user
        app.dependency_overrides[get_current_user_id] = override_get_current_user_id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            current_mock_user = owner_user

            # 1. Action: Task Created
            create_payload = {
                "title": "Build Real-time Task Pipeline",
                "description": "Initial task description for pipeline",
                "status": "todo",
                "priority": "high",
                "task_type": "feature",
                "project_id": project1.id,
                "assignee_id": owner_user.id,
            }
            res_create = await client.post("/api/v1/tasks", json=create_payload)
            assert res_create.status_code == 201, res_create.text
            task_data = res_create.json()
            task_id = task_data["id"]

            # Confirm creator and assignee payloads use real names, not IDs
            assert task_data["assignee"]["full_name"] == "Alice the Great"
            assert task_data["reporter"]["full_name"] == "Alice the Great"

            # Verify 'created' activity row
            async with session_factory() as s:
                act_rows = (await s.execute(select(TaskActivity).where(TaskActivity.task_id == task_id))).scalars().all()
                assert len(act_rows) == 1
                assert act_rows[0].action == "created"
                assert act_rows[0].actor_id == owner_user.id
                assert act_rows[0].organization_id == org1_id

            # 2. Action: Field changes (one row per field, trim description > 500 chars)
            very_long_desc = "x" * 600
            patch_payload = {
                "title": "Build Real-time Task Pipeline (Updated)",
                "description": very_long_desc,
                "priority": "urgent",
            }
            res_patch = await client.patch(f"/api/v1/tasks/{task_id}", json=patch_payload)
            assert res_patch.status_code == 200, res_patch.text

            async with session_factory() as s:
                act_rows = (await s.execute(
                    select(TaskActivity).where(TaskActivity.task_id == task_id).order_by(TaskActivity.created_at.asc())
                )).scalars().all()
                # 1 created + 3 field updates (title, description, priority) = 4
                assert len(act_rows) == 4
                field_names = [a.field_name for a in act_rows[1:]]
                assert set(field_names) == {"title", "description", "priority"}

                desc_act = next(a for a in act_rows if a.field_name == "description")
                assert desc_act.action == "field_updated"
                assert len(desc_act.new_value) == 500  # Trimmed to 500 characters!
                assert desc_act.new_value == "x" * 500

            # 3. Action: Status changed
            res_status = await client.patch(f"/api/v1/tasks/{task_id}/status", json={"status": "in_progress"})
            assert res_status.status_code == 200, res_status.text

            async with session_factory() as s:
                latest_act = (await s.execute(
                    select(TaskActivity).where(TaskActivity.task_id == task_id).order_by(TaskActivity.created_at.desc())
                )).scalars().first()
                assert latest_act.action == "status_changed"
                assert latest_act.field_name == "status"
                assert latest_act.old_value == "todo"
                assert latest_act.new_value == "in_progress"

            # 4. Action: Assignee changed
            res_assign = await client.patch(f"/api/v1/tasks/{task_id}", json={"assignee_id": member_user.id})
            assert res_assign.status_code == 200, res_assign.text

            async with session_factory() as s:
                latest_act = (await s.execute(
                    select(TaskActivity).where(TaskActivity.task_id == task_id).order_by(TaskActivity.created_at.desc())
                )).scalars().first()
                assert latest_act.action == "assignee_changed"
                assert latest_act.field_name == "assignee_id"
                assert latest_act.old_value == owner_user.id
                assert latest_act.new_value == member_user.id

            # 5. Action: Comment added, edited, deleted
            current_mock_user = member_user
            res_comment = await client.post(f"/api/v1/tasks/{task_id}/comments", json={"body": "Working on the schema now."})
            assert res_comment.status_code == 201, res_comment.text
            comm_data = res_comment.json()
            comm_id = comm_data["id"]

            # Author name in comment must be real name (display_name else full_name), Bob Member
            assert comm_data["author_name"] == "Bob Member"
            assert comm_data["author"]["full_name"] == "Bob Member"

            # Edit comment
            res_edit_comm = await client.patch(f"/api/v1/tasks/{task_id}/comments/{comm_id}", json={"body": "Schema updated with indexes."})
            assert res_edit_comm.status_code == 200, res_edit_comm.text

            # Delete comment
            res_del_comm = await client.delete(f"/api/v1/tasks/{task_id}/comments/{comm_id}")
            assert res_del_comm.status_code == 204

            async with session_factory() as s:
                comm_acts = (await s.execute(
                    select(TaskActivity).where(
                        TaskActivity.task_id == task_id,
                        TaskActivity.action.in_(["comment_added", "comment_edited", "comment_deleted"])
                    ).order_by(TaskActivity.created_at.asc())
                )).scalars().all()
                assert len(comm_acts) == 3
                assert comm_acts[0].action == "comment_added"
                assert comm_acts[1].action == "comment_edited"
                assert comm_acts[2].action == "comment_deleted"
                assert comm_acts[0].actor_id == member_user.id

            # 6. Action: Attachment added and removed
            current_mock_user = owner_user
            res_att = await client.post(
                f"/api/v1/tasks/{task_id}/attachments",
                json={
                    "filename": "architecture_diagram.png",
                    "file_size": 204800,
                    "mime_type": "image/png",
                    "storage_key": "s3://bucket/diagram.png"
                }
            )
            assert res_att.status_code == 201, res_att.text
            att_data = res_att.json()
            att_id = att_data["id"]

            res_del_att = await client.delete(f"/api/v1/tasks/{task_id}/attachments/{att_id}")
            assert res_del_att.status_code == 204

            async with session_factory() as s:
                att_acts = (await s.execute(
                    select(TaskActivity).where(
                        TaskActivity.task_id == task_id,
                        TaskActivity.action.in_(["attachment_added", "attachment_removed"])
                    ).order_by(TaskActivity.created_at.asc())
                )).scalars().all()
                assert len(att_acts) == 2
                assert att_acts[0].action == "attachment_added"
                assert att_acts[1].action == "attachment_removed"

            # 7. Action: Ticket linked and unlinked
            res_link = await client.post(f"/api/v1/tasks/{task_id}/tickets/{ticket1.id}")
            assert res_link.status_code == 200, res_link.text
            assert res_link.json()["status"] == "linked"

            res_unlink = await client.delete(f"/api/v1/tasks/{task_id}/tickets/{ticket1.id}")
            assert res_unlink.status_code == 200, res_unlink.text
            assert res_unlink.json()["status"] == "unlinked"

            async with session_factory() as s:
                ticket_acts = (await s.execute(
                    select(TaskActivity).where(
                        TaskActivity.task_id == task_id,
                        TaskActivity.action.in_(["ticket_linked", "ticket_unlinked"])
                    ).order_by(TaskActivity.created_at.asc())
                )).scalars().all()
                assert len(ticket_acts) == 2
                assert ticket_acts[0].action == "ticket_linked"
                assert ticket_acts[1].action == "ticket_unlinked"

            # 8. Activity endpoint: GET /api/v1/tasks/{task_id}/activity
            res_act = await client.get(f"/api/v1/tasks/{task_id}/activity?page=1&page_size=50")
            assert res_act.status_code == 200, res_act.text
            act_page = res_act.json()
            assert act_page["total"] > 0
            assert len(act_page["items"]) > 0

            # Verify no raw user IDs as actor names in any activity item
            for item in act_page["items"]:
                assert item["actor_name"] in ("Alice the Great", "Bob Member", "Deleted user")
                assert "usr_" not in item["actor_name"]
                assert item["summary"] is not None
                assert len(item["summary"]) > 0

            # 9. Organization isolation: foreign user cannot see activity
            current_mock_user = foreign_user
            res_foreign_act = await client.get(f"/api/v1/tasks/{task_id}/activity")
            assert res_foreign_act.status_code == 404

            # 10. Feed into Activity Logs page source (/api/v1/audit-logs/)
            current_mock_user = owner_user
            res_audit = await client.get("/api/v1/audit-logs/?time_horizon=all")
            assert res_audit.status_code == 200, res_audit.text
            audit_data = res_audit.json()
            assert audit_data["total"] > 0

            # Verify task activity appears in audit logs with real name
            task_audit_items = [it for it in audit_data["items"] if it.get("ticket_number") == task_data["task_number"]]
            assert len(task_audit_items) > 0
            for it in task_audit_items:
                if it.get("actor"):
                    assert it["actor"]["full_name"] in ("Alice the Great", "Bob Member")
                    assert "usr_" not in it["actor"]["full_name"]

    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
        # Drop isolated schema
        conn = psycopg2.connect(sync_direct_url)
        cur = conn.cursor()
        cur.execute(f"DROP SCHEMA IF EXISTS {test_schema} CASCADE;")
        conn.commit()
        conn.close()
