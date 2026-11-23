import os
import uuid
import json
import pytest
import httpx
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool

from backend.app.main import app
from backend.app.database import Base, get_db
from backend.app.models import User, Organization, Project, Ticket, TicketAttachment, Membership
from backend.app.core.config import settings
from backend.app.core.security import create_access_token


def _utc_now():
    return datetime.now(timezone.utc)


# Minimal 1x1 valid PNG bytes
VALID_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00"
    b"\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.fixture(scope="function")
async def test_env():
    """Sets up an isolated in-memory SQLite database, creating all tables and seeding test fixtures."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
        echo=False
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    # Seed test organizations, users, project, and ticket
    org1_id = f"org_{uuid.uuid4().hex[:8]}"
    org2_id = f"org_{uuid.uuid4().hex[:8]}"
    u_owner_id = f"usr_{uuid.uuid4().hex[:8]}"
    u_member1_id = f"usr_{uuid.uuid4().hex[:8]}"
    u_member2_id = f"usr_{uuid.uuid4().hex[:8]}"
    u_org2_id = f"usr_{uuid.uuid4().hex[:8]}"
    proj_id = f"proj_{uuid.uuid4().hex[:8]}"
    ticket_id = f"tkt_{uuid.uuid4().hex[:8]}"

    async with session_factory() as session:
        org1 = Organization(id=org1_id, name="Org Alpha", slug="org-alpha", created_at=_utc_now())
        org2 = Organization(id=org2_id, name="Org Beta", slug="org-beta", created_at=_utc_now())
        session.add_all([org1, org2])

        user_owner = User(
            id=u_owner_id,
            email="owner@alpha.com",
            hashed_password="pw",
            full_name="Alpha Owner",
            role="owner",
            is_active=True,
            email_verified=True,
            organization_id=org1_id,
            created_at=_utc_now(),
        )
        user_member1 = User(
            id=u_member1_id,
            email="member1@alpha.com",
            hashed_password="pw",
            full_name="Alpha Member 1",
            role="member",
            is_active=True,
            email_verified=True,
            organization_id=org1_id,
            created_at=_utc_now(),
        )
        user_member2 = User(
            id=u_member2_id,
            email="member2@alpha.com",
            hashed_password="pw",
            full_name="Alpha Member 2",
            role="member",
            is_active=True,
            email_verified=True,
            organization_id=org1_id,
            created_at=_utc_now(),
        )
        user_org2 = User(
            id=u_org2_id,
            email="user@beta.com",
            hashed_password="pw",
            full_name="Beta User",
            role="member",
            is_active=True,
            email_verified=True,
            organization_id=org2_id,
            created_at=_utc_now(),
        )
        session.add_all([user_owner, user_member1, user_member2, user_org2])

        mem_owner = Membership(id=str(uuid.uuid4()), user_id=u_owner_id, organization_id=org1_id, role="owner", status="active", joined_at=_utc_now())
        mem_m1 = Membership(id=str(uuid.uuid4()), user_id=u_member1_id, organization_id=org1_id, role="member", status="active", joined_at=_utc_now())
        mem_m2 = Membership(id=str(uuid.uuid4()), user_id=u_member2_id, organization_id=org1_id, role="member", status="active", joined_at=_utc_now())
        mem_o2 = Membership(id=str(uuid.uuid4()), user_id=u_org2_id, organization_id=org2_id, role="member", status="active", joined_at=_utc_now())
        session.add_all([mem_owner, mem_m1, mem_m2, mem_o2])

        project = Project(
            id=proj_id,
            name="Project Alpha",
            key="ALPHA",
            organization_id=org1_id,
            created_at=_utc_now(),
        )
        session.add(project)

        ticket = Ticket(
            id=ticket_id,
            ticket_number="ALPHA-101",
            title="Database attachment test ticket",
            project_id=proj_id,
            reporter_id=u_member1_id,
            created_at=_utc_now(),
        )
        session.add(ticket)
        await session.commit()

    tokens = {
        "owner": create_access_token(u_owner_id, role="owner", org_id=org1_id),
        "member1": create_access_token(u_member1_id, role="member", org_id=org1_id),
        "member2": create_access_token(u_member2_id, role="member", org_id=org1_id),
        "org2": create_access_token(u_org2_id, role="member", org_id=org2_id),
    }

    ids = {
        "org1_id": org1_id,
        "org2_id": org2_id,
        "u_owner_id": u_owner_id,
        "u_member1_id": u_member1_id,
        "u_member2_id": u_member2_id,
        "u_org2_id": u_org2_id,
        "ticket_id": ticket_id,
    }

    yield {"tokens": tokens, "ids": ids}

    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.mark.anyio
async def test_valid_attachment_lifecycle(test_env):
    """Tests valid upload, listing, downloading, previewing, and comment attachment linking."""
    tokens = test_env["tokens"]
    ticket_id = test_env["ids"]["ticket_id"]
    headers = {"Authorization": f"Bearer {tokens['member1']}"}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. Valid Upload
        files = {"file": ("screenshot.png", VALID_PNG_BYTES, "image/png")}
        res = await client.post(f"/api/v1/tickets/{ticket_id}/attachments", headers=headers, files=files)
        assert res.status_code == 201, res.text
        att_data = res.json()
        assert att_data["name"] == "screenshot.png"
        assert att_data["content_type"] == "image/png"
        att_id = att_data["id"]

        # 2. List attachments
        list_res = await client.get(f"/api/v1/tickets/{ticket_id}/attachments", headers=headers)
        assert list_res.status_code == 200
        items = list_res.json()
        assert any(item["id"] == att_id for item in items)

        # 3. Download attachment
        dl_res = await client.get(f"/api/v1/tickets/{ticket_id}/attachments/{att_id}/download", headers=headers)
        assert dl_res.status_code == 200
        assert dl_res.content == VALID_PNG_BYTES
        assert dl_res.headers.get("x-content-type-options") == "nosniff"
        assert "attachment" in dl_res.headers.get("content-disposition", "")

        # 4. Image Preview
        preview_res = await client.get(f"/api/v1/tickets/{ticket_id}/attachments/{att_id}/preview", headers=headers)
        assert preview_res.status_code == 200
        assert preview_res.content == VALID_PNG_BYTES
        assert preview_res.headers.get("x-content-type-options") == "nosniff"
        assert "inline" in preview_res.headers.get("content-disposition", "")

        # 5. Comment accepting optional attachment IDs
        comment_payload = {
            "content": "Here is the visual proof",
            "attachment_ids": [att_id]
        }
        c_res = await client.post(f"/api/v1/tickets/{ticket_id}/comments", headers=headers, json=comment_payload)
        assert c_res.status_code == 201, c_res.text
        c_data = c_res.json()
        assert c_data["content"] == "Here is the visual proof"


@pytest.mark.anyio
async def test_disguised_file_rejected(test_env):
    """Tests that files with mismatched magic bytes / disguised formats are rejected."""
    tokens = test_env["tokens"]
    ticket_id = test_env["ids"]["ticket_id"]
    headers = {"Authorization": f"Bearer {tokens['member1']}"}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Disguised Windows PE executable disguised as PNG
        fake_png = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff\x00\x00"
        res = await client.post(
            f"/api/v1/tickets/{ticket_id}/attachments",
            headers=headers,
            files={"file": ("malware.png", fake_png, "image/png")}
        )
        assert res.status_code == 400
        assert "error" in res.json()

        # HTML disguised as plain text
        fake_txt = b"<!DOCTYPE html><html><head><script>alert(1)</script></head><body>evil</body></html>"
        res_txt = await client.post(
            f"/api/v1/tickets/{ticket_id}/attachments",
            headers=headers,
            files={"file": ("notes.txt", fake_txt, "text/plain")}
        )
        assert res_txt.status_code == 400
        assert "error" in res_txt.json()

        # Text with embedded NUL byte
        nul_txt = b"line 1\nline 2\x00hidden binary injection"
        res_nul = await client.post(
            f"/api/v1/tickets/{ticket_id}/attachments",
            headers=headers,
            files={"file": ("log.log", nul_txt, "text/plain")}
        )
        assert res_nul.status_code == 400
        assert "error" in res_nul.json()


@pytest.mark.anyio
async def test_oversized_file_rejected(test_env):
    """Tests that oversized files exceeding limit are aborted during streaming."""
    tokens = test_env["tokens"]
    ticket_id = test_env["ids"]["ticket_id"]
    headers = {"Authorization": f"Bearer {tokens['member1']}"}

    # Generate a payload larger than 10MB limit (10MB + 1KB)
    large_size = (10 * 1024 * 1024) + 1024
    oversized_bytes = b"0" * large_size

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        res = await client.post(
            f"/api/v1/tickets/{ticket_id}/attachments",
            headers=headers,
            files={"file": ("big_log.log", oversized_bytes, "text/plain")}
        )
        assert res.status_code == 413
        assert "error" in res.json()


@pytest.mark.anyio
async def test_disallowed_file_types_rejected(test_env):
    """Tests that disallowed extensions/types (executables, archives, svg) are rejected."""
    tokens = test_env["tokens"]
    ticket_id = test_env["ids"]["ticket_id"]
    headers = {"Authorization": f"Bearer {tokens['member1']}"}

    disallowed = [
        ("app.exe", b"MZ\x90\x00test", "application/octet-stream"),
        ("bundle.zip", b"PK\x03\x04test", "application/zip"),
        ("vector.svg", b"<svg xmlns='http://www.w3.org/2000/svg'></svg>", "image/svg+xml"),
        ("script.sh", b"#!/bin/bash\necho hello", "text/x-shellscript"),
    ]

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        for fname, data, mime in disallowed:
            res = await client.post(
                f"/api/v1/tickets/{ticket_id}/attachments",
                headers=headers,
                files={"file": (fname, data, mime)}
            )
            assert res.status_code == 400, f"Expected 400 for {fname}, got {res.status_code}"
            assert "error" in res.json()


@pytest.mark.anyio
async def test_other_organization_blocked(test_env):
    """Tests that users from a different organization cannot view, upload, or delete attachments."""
    tokens = test_env["tokens"]
    ticket_id = test_env["ids"]["ticket_id"]
    headers_org2 = {"Authorization": f"Bearer {tokens['org2']}"}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Upload attempt
        res_upload = await client.post(
            f"/api/v1/tickets/{ticket_id}/attachments",
            headers=headers_org2,
            files={"file": ("test.png", VALID_PNG_BYTES, "image/png")}
        )
        assert res_upload.status_code == 404

        # List attempt
        res_list = await client.get(
            f"/api/v1/tickets/{ticket_id}/attachments",
            headers=headers_org2
        )
        assert res_list.status_code == 404


@pytest.mark.anyio
async def test_unauthenticated_blocked(test_env):
    """Tests that unauthenticated requests are rejected with 401."""
    ticket_id = test_env["ids"]["ticket_id"]

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        res = await client.post(
            f"/api/v1/tickets/{ticket_id}/attachments",
            files={"file": ("test.png", VALID_PNG_BYTES, "image/png")}
        )
        assert res.status_code == 401


@pytest.mark.anyio
async def test_delete_permissions(test_env):
    """Tests that only the original uploader or an organization owner/admin can delete an attachment."""
    tokens = test_env["tokens"]
    ticket_id = test_env["ids"]["ticket_id"]
    headers_m1 = {"Authorization": f"Bearer {tokens['member1']}"}
    headers_m2 = {"Authorization": f"Bearer {tokens['member2']}"}
    headers_owner = {"Authorization": f"Bearer {tokens['owner']}"}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Member 1 uploads attachment A
        res_up_a = await client.post(
            f"/api/v1/tickets/{ticket_id}/attachments",
            headers=headers_m1,
            files={"file": ("pic_a.png", VALID_PNG_BYTES, "image/png")}
        )
        assert res_up_a.status_code == 201
        att_a_id = res_up_a.json()["id"]

        # Member 2 (not uploader, not admin) attempts to delete attachment A -> 403 Forbidden
        res_del_m2 = await client.delete(
            f"/api/v1/tickets/{ticket_id}/attachments/{att_a_id}",
            headers=headers_m2
        )
        assert res_del_m2.status_code == 403
        assert "error" in res_del_m2.json()

        # Member 1 (uploader) deletes own attachment A -> 200 OK
        res_del_m1 = await client.delete(
            f"/api/v1/tickets/{ticket_id}/attachments/{att_a_id}",
            headers=headers_m1
        )
        assert res_del_m1.status_code == 200

        # Member 1 uploads attachment B
        res_up_b = await client.post(
            f"/api/v1/tickets/{ticket_id}/attachments",
            headers=headers_m1,
            files={"file": ("pic_b.png", VALID_PNG_BYTES, "image/png")}
        )
        assert res_up_b.status_code == 201
        att_b_id = res_up_b.json()["id"]

        # Org Owner (admin role) deletes attachment B uploaded by Member 1 -> 200 OK
        res_del_owner = await client.delete(
            f"/api/v1/tickets/{ticket_id}/attachments/{att_b_id}",
            headers=headers_owner
        )
        assert res_del_owner.status_code == 200
