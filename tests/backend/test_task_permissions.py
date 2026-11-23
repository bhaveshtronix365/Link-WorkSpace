import uuid
import pytest
from datetime import datetime, timezone
from httpx import AsyncClient, ASGITransport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from backend.app.main import app
from backend.app.core.config import settings
from backend.app.database import Base, get_db
from backend.app.dependencies import get_current_active_user
from backend.app.models import User, Organization, Task

# Direct non-pooled connection for test isolated schema
async_direct_url = settings.DATABASE_URL.replace('-pooler', '')
sync_direct_url = async_direct_url.replace('postgresql+asyncpg://', 'postgresql://').replace('?ssl=require', '?sslmode=require')

current_mock_user: User | None = None

def override_get_current_active_user():
    global current_mock_user
    return current_mock_user


@pytest.mark.anyio
async def test_task_permission_matrix():
    global current_mock_user

    test_schema = f"test_task_perm_{uuid.uuid4().hex[:8]}"

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
            full_name="Org Owner",
            role="owner",
            organization_id=org1_id,
            is_active=True,
        )
        admin_user = User(
            id=f"usr_admin_{uuid.uuid4().hex[:6]}",
            email=f"admin_{uuid.uuid4().hex[:4]}@example.com",
            hashed_password="hash",
            full_name="Org Admin",
            role="admin",
            organization_id=org1_id,
            is_active=True,
        )
        creator_member = User(
            id=f"usr_creator_{uuid.uuid4().hex[:6]}",
            email=f"creator_{uuid.uuid4().hex[:4]}@example.com",
            hashed_password="hash",
            full_name="Creator Member",
            role="member",
            organization_id=org1_id,
            is_active=True,
        )
        assignee_member = User(
            id=f"usr_assignee_{uuid.uuid4().hex[:6]}",
            email=f"assignee_{uuid.uuid4().hex[:4]}@example.com",
            hashed_password="hash",
            full_name="Assignee Member",
            role="member",
            organization_id=org1_id,
            is_active=True,
        )
        other_member = User(
            id=f"usr_other_{uuid.uuid4().hex[:6]}",
            email=f"other_{uuid.uuid4().hex[:4]}@example.com",
            hashed_password="hash",
            full_name="Other Member",
            role="member",
            organization_id=org1_id,
            is_active=True,
        )
        foreign_user = User(
            id=f"usr_foreign_{uuid.uuid4().hex[:6]}",
            email=f"foreign_{uuid.uuid4().hex[:4]}@example.com",
            hashed_password="hash",
            full_name="Foreign User",
            role="owner",
            organization_id=org2_id,
            is_active=True,
        )

        # Seed data
        async with session_factory() as session:
            org1 = Organization(id=org1_id, name="Test Org 1", slug=f"org1-{uuid.uuid4().hex[:6]}")
            org2 = Organization(id=org2_id, name="Test Org 2", slug=f"org2-{uuid.uuid4().hex[:6]}")
            session.add_all([org1, org2, owner_user, admin_user, creator_member, assignee_member, other_member, foreign_user])
            await session.commit()

        async def override_get_db():
            async with session_factory() as session:
                yield session

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_current_active_user] = override_get_current_active_user

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Owner creates a task assigned to assignee_member
            current_mock_user = owner_user
            create_res = await client.post(
                "/api/v1/tasks",
                json={
                    "title": "Owner Created Task",
                    "description": "Initial description by owner",
                    "assignee_id": assignee_member.id,
                    "status": "todo",
                    "priority": "medium",
                }
            )
            assert create_res.status_code == 201, create_res.text
            owner_task = create_res.json()
            task_id = owner_task["id"]
            assert owner_task["permissions"]["can_edit_details"] is True
            assert owner_task["permissions"]["can_change_status"] is True
            assert owner_task["permissions"]["can_delete"] is True

            # 2. Member (neither creator nor owner/admin) editing owner-created task's description -> 403 Forbidden
            current_mock_user = other_member
            patch_res = await client.patch(
                f"/api/v1/tasks/{task_id}",
                json={"description": "Hacked description by member"}
            )
            assert patch_res.status_code == 403, patch_res.text
            err = patch_res.json()
            assert err["detail"]["code"] == "task_edit_forbidden"

            # Assignee (not creator, not owner/admin) editing description -> 403 Forbidden
            current_mock_user = assignee_member
            patch_res2 = await client.patch(
                f"/api/v1/tasks/{task_id}",
                json={"description": "Assignee trying to edit description"}
            )
            assert patch_res2.status_code == 403, patch_res2.text
            assert patch_res2.json()["detail"]["code"] == "task_edit_forbidden"

            # 3. Assignee changing status -> SUCCESS (200 OK)
            current_mock_user = assignee_member
            status_res = await client.patch(
                f"/api/v1/tasks/{task_id}/status",
                json={"status": "in_progress"}
            )
            assert status_res.status_code == 200, status_res.text
            assert status_res.json()["status"] == "in_progress"

            # Assignee updating status via PATCH /{task_id} -> SUCCESS (200 OK)
            status_patch_res = await client.patch(
                f"/api/v1/tasks/{task_id}",
                json={"status": "in_review"}
            )
            assert status_patch_res.status_code == 200, status_patch_res.text
            assert status_patch_res.json()["status"] == "in_review"

            # Check permissions object seen by assignee
            get_res_assignee = await client.get(f"/api/v1/tasks/{task_id}")
            assert get_res_assignee.status_code == 200
            perms_assignee = get_res_assignee.json()["permissions"]
            assert perms_assignee["can_edit_details"] is False
            assert perms_assignee["can_change_status"] is True
            assert perms_assignee["can_delete"] is False

            # 4. Creator editing their own task -> SUCCESS (200 OK)
            current_mock_user = creator_member
            creator_task_res = await client.post(
                "/api/v1/tasks",
                json={
                    "title": "Creator's Task",
                    "description": "Original description",
                    "status": "todo",
                }
            )
            assert creator_task_res.status_code == 201
            creator_task_id = creator_task_res.json()["id"]

            edit_own_res = await client.patch(
                f"/api/v1/tasks/{creator_task_id}",
                json={
                    "title": "Creator Updated Own Task",
                    "description": "Creator updated own description",
                    "priority": "high",
                }
            )
            assert edit_own_res.status_code == 200, edit_own_res.text
            assert edit_own_res.json()["title"] == "Creator Updated Own Task"
            assert edit_own_res.json()["description"] == "Creator updated own description"

            # 5. Owner editing any task -> SUCCESS (200 OK)
            current_mock_user = owner_user
            owner_edit_res = await client.patch(
                f"/api/v1/tasks/{creator_task_id}",
                json={
                    "description": "Owner edited creator's task description",
                    "priority": "urgent",
                }
            )
            assert owner_edit_res.status_code == 200, owner_edit_res.text
            assert owner_edit_res.json()["description"] == "Owner edited creator's task description"
            assert owner_edit_res.json()["priority"] == "urgent"

            # Admin editing any task -> SUCCESS (200 OK)
            current_mock_user = admin_user
            admin_edit_res = await client.patch(
                f"/api/v1/tasks/{creator_task_id}",
                json={"title": "Admin Updated Title"}
            )
            assert admin_edit_res.status_code == 200, admin_edit_res.text
            assert admin_edit_res.json()["title"] == "Admin Updated Title"

            # 6. User from another organization -> 404 Not Found
            current_mock_user = foreign_user
            foreign_get = await client.get(f"/api/v1/tasks/{task_id}")
            assert foreign_get.status_code == 404, foreign_get.text

            foreign_patch = await client.patch(f"/api/v1/tasks/{task_id}", json={"title": "Foreign Exploit"})
            assert foreign_patch.status_code == 404, foreign_patch.text

            foreign_delete = await client.delete(f"/api/v1/tasks/{task_id}")
            assert foreign_delete.status_code == 404, foreign_delete.text

            # 7. Unknown or forbidden fields rejected
            current_mock_user = owner_user
            unknown_res = await client.patch(
                f"/api/v1/tasks/{task_id}",
                json={"unknown_field_123": "injected"}
            )
            assert unknown_res.status_code in (400, 422), unknown_res.text

            org_res = await client.patch(
                f"/api/v1/tasks/{task_id}",
                json={"organization_id": org2_id}
            )
            assert org_res.status_code in (400, 422), org_res.text

            # 8. Delete permission: member cannot delete (403), owner can (204)
            current_mock_user = other_member
            del_member_res = await client.delete(f"/api/v1/tasks/{task_id}")
            assert del_member_res.status_code == 403
            assert del_member_res.json()["detail"]["code"] == "task_edit_forbidden"

            current_mock_user = owner_user
            del_owner_res = await client.delete(f"/api/v1/tasks/{task_id}")
            assert del_owner_res.status_code == 204

    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
        # Drop isolated schema
        conn = psycopg2.connect(sync_direct_url)
        cur = conn.cursor()
        cur.execute(f"DROP SCHEMA IF EXISTS {test_schema} CASCADE;")
        conn.commit()
        conn.close()
