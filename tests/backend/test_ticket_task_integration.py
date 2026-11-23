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
from backend.app.models import User, Organization, Task, TaskActivity, Ticket, TicketActivity, Project, ProjectActivity

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
async def test_ticket_task_integration():
    global current_mock_user

    test_schema = f"test_tkt_tsk_{uuid.uuid4().hex[:8]}"

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

        user1 = User(
            id=f"usr_1_{uuid.uuid4().hex[:6]}",
            email=f"user1_{uuid.uuid4().hex[:4]}@example.com",
            hashed_password="hashed_pw_test_123",
            full_name="Alice Member",
            display_name="Alice M",
            role="member",
            organization_id=org1_id,
            is_active=True,
        )
        user2_other_org = User(
            id=f"usr_2_{uuid.uuid4().hex[:6]}",
            email=f"user2_{uuid.uuid4().hex[:4]}@example.com",
            hashed_password="hashed_pw_test_123",
            full_name="Bob Other",
            display_name="Bob O",
            role="member",
            organization_id=org2_id,
            is_active=True,
        )

        async with session_factory() as session:
            session.add(Organization(id=org1_id, name="Test Org 1", slug=f"org1-{uuid.uuid4().hex[:4]}"))
            session.add(Organization(id=org2_id, name="Test Org 2", slug=f"org2-{uuid.uuid4().hex[:4]}"))
            session.add(user1)
            session.add(user2_other_org)

            # Projects
            proj1 = Project(id=f"prj_1_{uuid.uuid4().hex[:6]}", name="Alpha Project", organization_id=org1_id)
            proj2_same_org = Project(id=f"prj_2_{uuid.uuid4().hex[:6]}", name="Beta Project", organization_id=org1_id)
            proj_other_org = Project(id=f"prj_other_{uuid.uuid4().hex[:6]}", name="Gamma Other", organization_id=org2_id)
            session.add_all([proj1, proj2_same_org, proj_other_org])

            p_suffix = uuid.uuid4().hex[:6].upper()
            tnum1 = f"TSK-101-{p_suffix}"
            tnum2 = f"TSK-102-{p_suffix}"
            tnum3 = f"TSK-103-{p_suffix}"
            tnum_other = f"TSK-999-{p_suffix}"

            # Tasks
            task1 = Task(
                id=f"tsk_1_{uuid.uuid4().hex[:6]}",
                task_number=tnum1,
                title="Implement Auth Feature",
                status="in_progress",
                priority="high",
                project_id=proj1.id,
                organization_id=org1_id,
                assignee_id=user1.id,
                reporter_id=user1.id,
            )
            task2_same_proj = Task(
                id=f"tsk_2_{uuid.uuid4().hex[:6]}",
                task_number=tnum2,
                title="Design Database Schema",
                status="todo",
                priority="medium",
                project_id=proj1.id,
                organization_id=org1_id,
                assignee_id=user1.id,
                reporter_id=user1.id,
            )
            task3_diff_proj = Task(
                id=f"tsk_3_{uuid.uuid4().hex[:6]}",
                task_number=tnum3,
                title="Beta Task",
                status="todo",
                priority="low",
                project_id=proj2_same_org.id,
                organization_id=org1_id,
                assignee_id=user1.id,
                reporter_id=user1.id,
            )
            task_other_org = Task(
                id=f"tsk_other_{uuid.uuid4().hex[:6]}",
                task_number=tnum_other,
                title="External Task",
                status="todo",
                priority="medium",
                project_id=proj_other_org.id,
                organization_id=org2_id,
                assignee_id=user2_other_org.id,
                reporter_id=user2_other_org.id,
            )
            session.add_all([task1, task2_same_proj, task3_diff_proj, task_other_org])
            await session.commit()

        # Override dependency
        async def override_get_db():
            async with session_factory() as session:
                yield session

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_current_active_user] = override_get_current_active_user
        app.dependency_overrides[get_current_user_id] = override_get_current_user_id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            current_mock_user = user1

            # -------------------------------------------------------------
            # 1. Validation on Ticket Create
            # -------------------------------------------------------------
            # A) Task does not exist -> 422
            r = await client.post("/api/v1/tickets", json={
                "title": "Bug in login",
                "task_id": "nonexistent_task_id",
                "project_id": proj1.id,
                "severity": "major"
            })
            assert r.status_code == 422, f"Expected 422 for non-existent task, got {r.status_code}: {r.text}"

            # B) Task belongs to another organization -> 422
            r = await client.post("/api/v1/tickets", json={
                "title": "Cross org bug",
                "task_id": task_other_org.id,
                "project_id": proj1.id,
                "severity": "major"
            })
            assert r.status_code == 422, f"Expected 422 for cross-org task, got {r.status_code}: {r.text}"

            # C) Task belongs to a different project than specified -> 422
            r = await client.post("/api/v1/tickets", json={
                "title": "Mismatched project ticket",
                "task_id": task3_diff_proj.id,
                "project_id": proj1.id,  # task3 is in proj2_same_org
                "severity": "major"
            })
            assert r.status_code == 422, f"Expected 422 for project mismatch, got {r.status_code}: {r.text}"

            # D) Neither project_id nor task_id provided -> 422
            r = await client.post("/api/v1/tickets", json={
                "title": "Missing project and task",
                "severity": "major"
            })
            assert r.status_code == 422, f"Expected 422 for missing project and task, got {r.status_code}: {r.text}"

            # E) Successful ticket create with task_id AND no project_id (inherits project from task)
            r = await client.post("/api/v1/tickets", json={
                "title": "Valid ticket linked to Task 1",
                "task_id": task1.id,
                "severity": "critical"
            })
            assert r.status_code == 201, f"Expected 201, got {r.status_code}: {r.text}"
            ticket1 = r.json()
            assert ticket1["task_id"] == task1.id
            assert ticket1["project_id"] == proj1.id
            assert ticket1["task"] is not None
            assert ticket1["task"]["id"] == task1.id
            assert ticket1["task"]["key"] == tnum1
            assert ticket1["task"]["title"] == "Implement Auth Feature"
            assert ticket1["task"]["status"] == "in_progress"
            assert ticket1["task"]["assignee_name"] == "Alice M"  # Real name, not raw user ID!

            # Verify activities recorded for linking on create
            async with session_factory() as session:
                t_act = (await session.execute(
                    select(TicketActivity).where(TicketActivity.ticket_id == ticket1["id"], TicketActivity.action == "task_linked")
                )).scalars().first()
                assert t_act is not None
                assert t_act.new_value == tnum1

                task_act = (await session.execute(
                    select(TaskActivity).where(TaskActivity.task_id == task1.id, TaskActivity.action == "ticket_linked")
                )).scalars().first()
                assert task_act is not None
                assert task_act.new_value == ticket1["ticket_number"]

            # F) Create a second ticket linked to Task 1
            r2 = await client.post("/api/v1/tickets", json={
                "title": "Second ticket linked to Task 1",
                "task_id": task1.id,
                "project_id": proj1.id,
                "severity": "major"
            })
            assert r2.status_code == 201
            ticket2 = r2.json()

            # -------------------------------------------------------------
            # 2. Task responses include tickets_count and open_tickets_count
            # -------------------------------------------------------------
            r_task = await client.get(f"/api/v1/tasks/{task1.id}")
            assert r_task.status_code == 200
            task_data = r_task.json()
            assert task_data["tickets_count"] == 2
            assert task_data["open_tickets_count"] == 2

            # -------------------------------------------------------------
            # 3. GET /api/v1/tickets?task_id={task_id} filter
            # -------------------------------------------------------------
            r_filtered = await client.get(f"/api/v1/tickets?task_id={task1.id}")
            assert r_filtered.status_code == 200
            t_list = r_filtered.json()
            assert len(t_list) == 2
            assert all(t["task_id"] == task1.id for t in t_list)

            # Filter with non-matching task_id
            r_empty = await client.get(f"/api/v1/tickets?task_id={task2_same_proj.id}")
            assert r_empty.status_code == 200
            assert len(r_empty.json()) == 0

            # -------------------------------------------------------------
            # 4. GET /api/v1/tasks/{id}/tickets (paginated, organization scoped)
            # -------------------------------------------------------------
            r_task_tickets = await client.get(f"/api/v1/tasks/{task1.id}/tickets?page=1&page_size=10")
            assert r_task_tickets.status_code == 200
            paginated = r_task_tickets.json()
            assert paginated["total"] == 2
            assert len(paginated["items"]) == 2
            assert paginated["items"][0]["task"]["key"] == tnum1

            # Cross-organization access to task tickets -> 404
            current_mock_user = user2_other_org
            r_cross_task_tickets = await client.get(f"/api/v1/tasks/{task1.id}/tickets")
            assert r_cross_task_tickets.status_code == 404
            current_mock_user = user1

            # -------------------------------------------------------------
            # 5. Validation on Ticket Update (Linking / Changing / Unlinking)
            # -------------------------------------------------------------
            # A) Update to task in different project -> 422
            r_up_diff = await client.patch(f"/api/v1/tickets/{ticket1['id']}", json={
                "task_id": task3_diff_proj.id
            })
            assert r_up_diff.status_code == 422

            # B) Update to task in different organization -> 422
            r_up_org = await client.patch(f"/api/v1/tickets/{ticket1['id']}", json={
                "task_id": task_other_org.id
            })
            assert r_up_org.status_code == 422

            # C) Update to another valid task in same project (task1 -> task2_same_proj)
            r_up_valid = await client.patch(f"/api/v1/tickets/{ticket1['id']}", json={
                "task_id": task2_same_proj.id
            })
            assert r_up_valid.status_code == 200
            updated_ticket1 = r_up_valid.json()
            assert updated_ticket1["task_id"] == task2_same_proj.id
            assert updated_ticket1["task"]["key"] == tnum2

            # Check activity log: task1 should have ticket_unlinked, task2 should have ticket_linked
            async with session_factory() as session:
                unlinked_act = (await session.execute(
                    select(TaskActivity).where(
                        TaskActivity.task_id == task1.id,
                        TaskActivity.action == "ticket_unlinked"
                    )
                )).scalars().first()
                assert unlinked_act is not None
                assert unlinked_act.old_value == ticket1["ticket_number"]

                linked_act = (await session.execute(
                    select(TaskActivity).where(
                        TaskActivity.task_id == task2_same_proj.id,
                        TaskActivity.action == "ticket_linked"
                    )
                )).scalars().first()
                assert linked_act is not None
                assert linked_act.new_value == ticket1["ticket_number"]

            # D) Unlink ticket (task_id = None)
            r_unlink = await client.patch(f"/api/v1/tickets/{ticket1['id']}", json={
                "task_id": None
            })
            assert r_unlink.status_code == 200
            unlinked_ticket = r_unlink.json()
            assert unlinked_ticket["task_id"] is None
            assert unlinked_ticket["task"] is None

            # Verify unlinked activities recorded
            async with session_factory() as session:
                t_unlinked_act = (await session.execute(
                    select(TicketActivity).where(
                        TicketActivity.ticket_id == ticket1["id"],
                        TicketActivity.action == "task_unlinked"
                    )
                )).scalars().all()
                assert len(t_unlinked_act) >= 1

            # -------------------------------------------------------------
            # 6. Deleting a task keeps its tickets (task_id becomes null)
            # -------------------------------------------------------------
            # ticket2 is still linked to task1
            r_del_task = await client.delete(f"/api/v1/tasks/{task1.id}")
            assert r_del_task.status_code == 204

            # Verify task is deleted
            async with session_factory() as session:
                t_deleted = (await session.execute(select(Task).where(Task.id == task1.id))).scalars().first()
                assert t_deleted is None

                # Verify ticket2 persists with task_id set to None
                t2_fresh = (await session.execute(select(Ticket).where(Ticket.id == ticket2["id"]))).scalars().first()
                assert t2_fresh is not None
                assert t2_fresh.task_id is None

                # Verify TicketActivity logged task_unlinked
                t2_act = (await session.execute(
                    select(TicketActivity).where(
                        TicketActivity.ticket_id == ticket2["id"],
                        TicketActivity.action == "task_unlinked"
                    )
                )).scalars().first()
                assert t2_act is not None
                assert t2_act.old_value == tnum1

                # Verify ProjectActivity or AuditLog logged task_deleted
                p_act = (await session.execute(
                    select(ProjectActivity).where(
                        ProjectActivity.target_id == task1.id,
                        ProjectActivity.action == "task_deleted"
                    )
                )).scalars().first()
                assert p_act is not None
                assert tnum1 in p_act.details

    finally:
        # Clean up isolated schema
        async with engine.begin() as db_conn:
            await db_conn.execute(text(f"DROP SCHEMA IF EXISTS {test_schema} CASCADE;"))
        await engine.dispose()
