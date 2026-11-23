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
from backend.app.models import User, Organization, Task, TaskActivity, Ticket, TicketActivity, Project, Membership

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
async def test_create_task_improvements():
    global current_mock_user

    test_schema = f"test_crt_tsk_{uuid.uuid4().hex[:8]}"

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

        # Creator / Assigner
        user1 = User(
            id=f"usr_1_{uuid.uuid4().hex[:6]}",
            email=f"creator_{uuid.uuid4().hex[:4]}@example.com",
            hashed_password="hash",
            full_name="Creator User",
            role="admin",
            organization_id=org1_id,
            is_active=True,
        )

        # Assignee whose membership is via Membership table (User.organization_id is None)
        user_member = User(
            id=f"usr_m_{uuid.uuid4().hex[:6]}",
            email=f"member_{uuid.uuid4().hex[:4]}@example.com",
            hashed_password="hash",
            full_name="Org Member",
            role="member",
            organization_id=None,
            is_active=True,
        )

        # Foreign user from org2
        foreign_user = User(
            id=f"usr_f_{uuid.uuid4().hex[:6]}",
            email=f"foreign_{uuid.uuid4().hex[:4]}@example.com",
            hashed_password="hash",
            full_name="Foreign User",
            role="member",
            organization_id=org2_id,
            is_active=True,
        )

        org1 = Organization(id=org1_id, name="Test Org 1", slug=f"org1-{uuid.uuid4().hex[:4]}")
        org2 = Organization(id=org2_id, name="Test Org 2", slug=f"org2-{uuid.uuid4().hex[:4]}")

        membership1 = Membership(
            id=f"mem_{uuid.uuid4().hex[:6]}",
            user_id=user_member.id,
            organization_id=org1_id,
            role="member",
            status="active",
        )

        proj1 = Project(
            id=f"proj_1_{uuid.uuid4().hex[:6]}",
            name="Project Alpha",
            key="ALPHA",
            organization_id=org1_id,
            lead_id=user1.id,
        )

        proj2 = Project(
            id=f"proj_2_{uuid.uuid4().hex[:6]}",
            name="Project Beta",
            key="BETA",
            organization_id=org1_id,
            lead_id=user1.id,
        )

        proj_foreign = Project(
            id=f"proj_f_{uuid.uuid4().hex[:6]}",
            name="Project Foreign",
            key="FOR",
            organization_id=org2_id,
            lead_id=foreign_user.id,
        )

        ticket1 = Ticket(
            id=f"tkt_1_{uuid.uuid4().hex[:6]}",
            project_id=proj1.id,
            title="Ticket Alpha 1",
            ticket_number=f"ALPHA-1-{uuid.uuid4().hex[:4]}",
            status="open",
            severity="medium",
            reporter_id=user1.id,
        )

        ticket2 = Ticket(
            id=f"tkt_2_{uuid.uuid4().hex[:6]}",
            project_id=proj1.id,
            title="Ticket Alpha 2",
            ticket_number=f"ALPHA-2-{uuid.uuid4().hex[:4]}",
            status="open",
            severity="high",
            reporter_id=user1.id,
        )

        ticket_other_proj = Ticket(
            id=f"tkt_p2_{uuid.uuid4().hex[:6]}",
            project_id=proj2.id,
            title="Ticket Beta 1",
            ticket_number=f"BETA-1-{uuid.uuid4().hex[:4]}",
            status="open",
            severity="low",
            reporter_id=user1.id,
        )

        ticket_foreign = Ticket(
            id=f"tkt_for_{uuid.uuid4().hex[:6]}",
            project_id=proj_foreign.id,
            title="Ticket Foreign 1",
            ticket_number=f"FOR-1-{uuid.uuid4().hex[:4]}",
            status="open",
            severity="low",
            reporter_id=foreign_user.id,
        )

        async with session_factory() as sess:
            sess.add_all([
                org1, org2, user1, user_member, foreign_user, membership1,
                proj1, proj2, proj_foreign, ticket1, ticket2, ticket_other_proj, ticket_foreign
            ])
            await sess.commit()

        # Wire overrides
        async def override_get_db():
            async with session_factory() as session:
                yield session

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_current_active_user] = override_get_current_active_user
        app.dependency_overrides[get_current_user_id] = override_get_current_user_id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            current_mock_user = user1

            # 1. Reject foreign assignee
            res_foreign_assignee = await client.post(
                "/api/v1/tasks",
                json={
                    "title": "Task with foreign assignee",
                    "project_id": proj1.id,
                    "assignee_id": foreign_user.id,
                }
            )
            assert res_foreign_assignee.status_code == 400, res_foreign_assignee.text

            # 2. Reject mismatched project ticket
            res_mismatched_ticket = await client.post(
                "/api/v1/tasks",
                json={
                    "title": "Task with cross-project ticket",
                    "project_id": proj1.id,
                    "ticket_ids": [ticket_other_proj.id],
                }
            )
            assert res_mismatched_ticket.status_code == 422, res_mismatched_ticket.text

            # 3. Reject foreign org ticket (403 Forbidden)
            res_foreign_ticket = await client.post(
                "/api/v1/tasks",
                json={
                    "title": "Task with foreign ticket",
                    "project_id": proj1.id,
                    "ticket_ids": [ticket_foreign.id],
                }
            )
            assert res_foreign_ticket.status_code == 403, res_foreign_ticket.text

            # 4. Successfully create task with Membership-based assignee and multiple tickets
            res_success = await client.post(
                "/api/v1/tasks",
                json={
                    "title": "Task with related tickets & assignee",
                    "description": "Integration test task",
                    "project_id": proj1.id,
                    "assignee_id": user_member.id,
                    "ticket_ids": [ticket1.id, ticket2.id],
                }
            )
            assert res_success.status_code == 201, res_success.text
            task_data = res_success.json()
            task_id = task_data["id"]
            assert task_data["assignee"]["id"] == user_member.id
            assert task_data["tickets_count"] == 2

            # Verify in DB: tickets are linked, and both TaskActivity and TicketActivity are created
            async with session_factory() as sess:
                db_t1 = (await sess.execute(select(Ticket).where(Ticket.id == ticket1.id))).scalars().first()
                db_t2 = (await sess.execute(select(Ticket).where(Ticket.id == ticket2.id))).scalars().first()
                assert db_t1.task_id == task_id
                assert db_t2.task_id == task_id

                t_acts = (await sess.execute(select(TaskActivity).where(TaskActivity.task_id == task_id, TaskActivity.action == "ticket_linked"))).scalars().all()
                assert len(t_acts) == 2

                tk_acts = (await sess.execute(select(TicketActivity).where(TicketActivity.ticket_id.in_([ticket1.id, ticket2.id]), TicketActivity.action == "task_linked"))).scalars().all()
                assert len(tk_acts) == 2

            # 5. Verify GET /api/v1/tasks/{task_id}/tickets
            res_linked_tickets = await client.get(f"/api/v1/tasks/{task_id}/tickets")
            assert res_linked_tickets.status_code == 200, res_linked_tickets.text
            linked_items = res_linked_tickets.json()["items"]
            assert len(linked_items) == 2
            linked_ids = {t["id"] for t in linked_items}
            assert ticket1.id in linked_ids
            assert ticket2.id in linked_ids

            # 6. Verify link_ticket idempotency (linking already-linked ticket)
            res_relink = await client.post(f"/api/v1/tasks/{task_id}/tickets/{ticket1.id}")
            assert res_relink.status_code == 200, res_relink.text
            relink_json = res_relink.json()
            assert relink_json.get("status") == "ok"

            # Check that no duplicate activity was created
            async with session_factory() as sess:
                t_acts_after = (await sess.execute(select(TaskActivity).where(TaskActivity.task_id == task_id, TaskActivity.action == "ticket_linked"))).scalars().all()
                assert len(t_acts_after) == 2

    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
        # Drop test schema
        conn = psycopg2.connect(sync_direct_url)
        cur = conn.cursor()
        cur.execute(f"DROP SCHEMA IF EXISTS {test_schema} CASCADE;")
        conn.commit()
        conn.close()
