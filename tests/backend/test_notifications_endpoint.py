"""
Comprehensive test suite for notifications endpoint using direct connection and isolated schema:
- Verifies GET /api/v1/notifications returns 200 with ticket and user targets resolved cleanly.
- Verifies target_label resolves to ticket_number (e.g. TKT-1001) instead of raw UUID.
- Verifies GET /api/v1/notifications/unread-count matches genuine unread notification count.
- Verifies GET /api/v1/notifications/inbox-count counts actionable notifications.
- Verifies PATCH /api/v1/notifications/{id}/read marks individual notification read and updates count.
- Verifies PATCH /api/v1/notifications/read-all marks all notifications read.
- Verifies strict multi-tenant scoping (notifications cannot be viewed or modified across organizations).
"""
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
from backend.app.models import User, Organization, Project, Ticket, Notification, Membership

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
async def test_notifications_endpoint_comprehensive():
    global current_mock_user

    test_schema = f"test_notif_{uuid.uuid4().hex[:8]}"

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

        user_org1 = User(
            id=f"usr_1_{uuid.uuid4().hex[:6]}",
            email=f"user1_{uuid.uuid4().hex[:4]}@example.com",
            hashed_password="hash",
            full_name="User Org One",
            role="admin",
            organization_id=org1_id,
            is_active=True,
        )

        user_org2 = User(
            id=f"usr_2_{uuid.uuid4().hex[:6]}",
            email=f"user2_{uuid.uuid4().hex[:4]}@example.com",
            hashed_password="hash",
            full_name="User Org Two",
            role="admin",
            organization_id=org2_id,
            is_active=True,
        )

        org1 = Organization(id=org1_id, name="Test Org 1", slug=f"org1-{uuid.uuid4().hex[:4]}")
        org2 = Organization(id=org2_id, name="Test Org 2", slug=f"org2-{uuid.uuid4().hex[:4]}")

        project1 = Project(
            id=f"proj_{uuid.uuid4().hex[:8]}",
            name="Project 1",
            key="PRJ",
            organization_id=org1_id,
        )

        tkt_num = f"PRJ-{uuid.uuid4().hex[:6].upper()}"
        ticket1 = Ticket(
            id=f"tkt_{uuid.uuid4().hex[:8]}",
            ticket_number=tkt_num,
            title="Fix notification bug",
            severity="high",
            status="open",
            project_id=project1.id,
        )

        notif_ticket = Notification(
            id=f"notif_tkt_{uuid.uuid4().hex[:6]}",
            user_id=user_org1.id,
            organization_id=org1_id,
            type="ticket_assigned",
            title="Ticket Assigned",
            body=f"You were assigned {tkt_num}",
            target_type="ticket",
            target_id=ticket1.id,
            is_read=False,
        )

        notif_join = Notification(
            id=f"notif_join_{uuid.uuid4().hex[:6]}",
            user_id=user_org1.id,
            organization_id=org1_id,
            type="join_request_decided",
            title="Join Request Approved",
            body="Welcome to the organization",
            target_type="join_request",
            target_id="req_123",
            is_read=False,
        )

        notif_org2 = Notification(
            id=f"notif_org2_{uuid.uuid4().hex[:6]}",
            user_id=user_org2.id,
            organization_id=org2_id,
            type="system",
            title="Org 2 Confidential Alert",
            body="For org 2 eyes only",
            target_type="system",
            is_read=False,
        )

        async with session_factory() as session:
            session.add_all([org1, org2, user_org1, user_org2, project1, ticket1, notif_ticket, notif_join, notif_org2])
            await session.commit()

        async def override_get_db():
            async with session_factory() as session:
                yield session

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_current_active_user] = override_get_current_active_user
        app.dependency_overrides[get_current_user_id] = override_get_current_user_id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Test as user_org1: unread-count
            current_mock_user = user_org1
            res_unread = await client.get("/api/v1/notifications/unread-count")
            assert res_unread.status_code == 200
            data_unread = res_unread.json()
            assert data_unread["unread_count"] == 2
            assert data_unread["sla_breach_count"] == 0

            # 2. Test as user_org1: inbox-count (ticket_assigned is actionable, join_request_decided is not)
            res_inbox = await client.get("/api/v1/notifications/inbox-count")
            assert res_inbox.status_code == 200
            assert res_inbox.json()["inbox_count"] == 1

            # 3. Test as user_org1: list notifications
            res_list = await client.get("/api/v1/notifications")
            assert res_list.status_code == 200
            data_list = res_list.json()
            assert data_list["total"] == 2
            assert data_list["unread_count"] == 2
            assert len(data_list["items"]) == 2

            # Check target_label resolution
            tkt_item = next(i for i in data_list["items"] if i["id"] == notif_ticket.id)
            assert tkt_item["target_label"] == tkt_num
            assert tkt_item["target_label"] != ticket1.id

            join_item = next(i for i in data_list["items"] if i["id"] == notif_join.id)
            assert join_item["target_label"] == "Join Request"

            # 4. Mark single notification read
            res_mark = await client.patch(f"/api/v1/notifications/{notif_ticket.id}/read")
            assert res_mark.status_code == 200
            mark_data = res_mark.json()
            assert mark_data["success"] is True
            assert mark_data["unread_count"] == 1

            # Verify unread count is updated
            res_unread_after = await client.get("/api/v1/notifications/unread-count")
            assert res_unread_after.json()["unread_count"] == 1

            # 5. Mark all read
            res_read_all = await client.patch("/api/v1/notifications/read-all")
            assert res_read_all.status_code == 200
            assert res_read_all.json()["marked_count"] == 1

            res_unread_final = await client.get("/api/v1/notifications/unread-count")
            assert res_unread_final.json()["unread_count"] == 0

            # 6. Test multi-tenant isolation: user_org2 cannot see user_org1's notifications
            current_mock_user = user_org2
            res_list_org2 = await client.get("/api/v1/notifications")
            assert res_list_org2.status_code == 200
            data_org2 = res_list_org2.json()
            assert data_org2["total"] == 1
            assert data_org2["items"][0]["title"] == "Org 2 Confidential Alert"

            # user_org2 cannot mark user_org1's notification as read
            res_patch_cross = await client.patch(f"/api/v1/notifications/{notif_ticket.id}/read")
            assert res_patch_cross.status_code == 404

    finally:
        app.dependency_overrides.clear()
        current_mock_user = None
        await engine.dispose()
        # Drop test schema
        conn = psycopg2.connect(sync_direct_url)
        cur = conn.cursor()
        cur.execute(f"DROP SCHEMA IF EXISTS {test_schema} CASCADE;")
        conn.commit()
        conn.close()
