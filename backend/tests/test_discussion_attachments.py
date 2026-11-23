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
from backend.app.models import User, Organization, Project, Discussion, DiscussionComment, TicketAttachment, Membership
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

    org1_id = f"org_{uuid.uuid4().hex[:8]}"
    org2_id = f"org_{uuid.uuid4().hex[:8]}"
    u_owner_id = f"usr_{uuid.uuid4().hex[:8]}"
    u_member1_id = f"usr_{uuid.uuid4().hex[:8]}"
    u_member2_id = f"usr_{uuid.uuid4().hex[:8]}"
    u_org2_id = f"usr_{uuid.uuid4().hex[:8]}"
    proj_id = f"proj_{uuid.uuid4().hex[:8]}"
    disc_id = f"disc_{uuid.uuid4().hex[:8]}"

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

        m_owner = Membership(id=str(uuid.uuid4()), user_id=u_owner_id, organization_id=org1_id, role="owner", status="active", joined_at=_utc_now())
        m_mem1 = Membership(id=str(uuid.uuid4()), user_id=u_member1_id, organization_id=org1_id, role="member", status="active", joined_at=_utc_now())
        m_mem2 = Membership(id=str(uuid.uuid4()), user_id=u_member2_id, organization_id=org1_id, role="member", status="active", joined_at=_utc_now())
        m_org2 = Membership(id=str(uuid.uuid4()), user_id=u_org2_id, organization_id=org2_id, role="member", status="active", joined_at=_utc_now())
        session.add_all([m_owner, m_mem1, m_mem2, m_org2])

        project = Project(
            id=proj_id,
            name="Project Alpha",
            key="ALPHA",
            organization_id=org1_id,
            lead_id=u_owner_id,
            created_at=_utc_now(),
        )
        session.add(project)

        discussion = Discussion(
            id=disc_id,
            project_id=proj_id,
            author_id=u_member1_id,
            title="Initial Architecture Discussion",
            content="RFC on database schema and async patterns.",
            category="rfc",
            created_at=_utc_now(),
        )
        session.add(discussion)
        await session.commit()

    tokens = {
        "owner": create_access_token(u_owner_id, role="owner", org_id=org1_id),
        "member1": create_access_token(u_member1_id, role="member", org_id=org1_id),
        "member2": create_access_token(u_member2_id, role="member", org_id=org1_id),
        "org2_user": create_access_token(u_org2_id, role="member", org_id=org2_id),
    }

    yield {
        "tokens": tokens,
        "users": {"owner": u_owner_id, "member1": u_member1_id, "member2": u_member2_id, "org2_user": u_org2_id},
        "org1_id": org1_id,
        "org2_id": org2_id,
        "project_id": proj_id,
        "discussion_id": disc_id,
    }

    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.mark.anyio
async def test_discussion_attachment_security_rules(test_env):
    """Verifies allowlist, magic-byte checks, and rejection of dangerous files."""
    tokens = test_env["tokens"]
    headers = {"Authorization": f"Bearer {tokens['member1']}"}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Valid PNG upload succeeds
        files = {"file": ("test_diagram.png", VALID_PNG_BYTES, "image/png")}
        res = await client.post("/api/v1/discussions/attachments", files=files, headers=headers)
        assert res.status_code == 201, res.text
        png_data = res.json()
        assert png_data["original_name"] == "test_diagram.png"
        assert png_data["content_type"] == "image/png"

        # 2. Valid text upload succeeds
        files = {"file": ("system.log", b"timestamp=2026-11-22 status=OK\n", "text/plain")}
        res = await client.post("/api/v1/discussions/attachments", files=files, headers=headers)
        assert res.status_code == 201
        log_data = res.json()
        assert log_data["content_type"] == "text/plain"

        # 3. Spoofed extension (text content named as png) fails magic bytes check
        files = {"file": ("fake_image.png", b"This is not a png file at all", "image/png")}
        res = await client.post("/api/v1/discussions/attachments", files=files, headers=headers)
        assert res.status_code == 400

        # 4. Executable / script upload fails
        files = {"file": ("malicious.exe", b"MZ\x90\x00\x03\x00\x00\x00", "application/octet-stream")}
        res = await client.post("/api/v1/discussions/attachments", files=files, headers=headers)
        assert res.status_code == 400


@pytest.mark.anyio
async def test_create_discussion_and_reply_with_attachments(test_env):
    """Verifies creating discussions and replies with linked attachments."""
    tokens = test_env["tokens"]
    headers = {"Authorization": f"Bearer {tokens['member1']}"}
    proj_id = test_env["project_id"]

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Pre-upload two files for a new discussion
        f1 = {"file": ("spec.png", VALID_PNG_BYTES, "image/png")}
        res1 = await client.post("/api/v1/discussions/attachments", files=f1, headers=headers)
        assert res1.status_code == 201
        att1_id = res1.json()["id"]

        f2 = {"file": ("notes.txt", b"Deployment checklist step 1 2 3", "text/plain")}
        res2 = await client.post("/api/v1/discussions/attachments", files=f2, headers=headers)
        assert res2.status_code == 201
        att2_id = res2.json()["id"]

        # Create discussion with attachment_ids
        payload = {
            "title": "RFC: Distributed Cache Strategy",
            "content": "Please review the attached diagram and deployment notes.",
            "category": "rfc",
            "attachment_ids": [att1_id, att2_id],
        }
        create_res = await client.post(
            f"/api/v1/discussions?project_id={proj_id}",
            json=payload,
            headers=headers
        )
        assert create_res.status_code == 201, create_res.text
        disc_data = create_res.json()
        assert len(disc_data["attachments"]) == 2
        returned_ids = {a["id"] for a in disc_data["attachments"]}
        assert att1_id in returned_ids
        assert att2_id in returned_ids

        disc_id = disc_data["id"]

        # Now add a reply with an attachment
        f3 = {"file": ("reply_diag.png", VALID_PNG_BYTES, "image/png")}
        res3 = await client.post(f"/api/v1/discussions/{disc_id}/attachments", files=f3, headers=headers)
        assert res3.status_code == 201
        att3_id = res3.json()["id"]

        reply_payload = {
            "content": "Here is the revised subnet architecture.",
            "attachment_ids": [att3_id],
        }
        reply_res = await client.post(
            f"/api/v1/discussions/{disc_id}/comments",
            json=reply_payload,
            headers=headers
        )
        assert reply_res.status_code == 201, reply_res.text
        comment_data = reply_res.json()
        assert len(comment_data["attachments"]) == 1
        assert comment_data["attachments"][0]["id"] == att3_id

        # Retrieve discussion thread - check clean separation of topic vs reply attachments
        get_res = await client.get(f"/api/v1/discussions/{disc_id}", headers=headers)
        assert get_res.status_code == 200
        thread = get_res.json()
        # Topic attachments
        assert len(thread["attachments"]) == 2
        # Reply attachments
        assert len(thread["comments"]) == 1
        assert len(thread["comments"][0]["attachments"]) == 1


@pytest.mark.anyio
async def test_discussion_attachment_download_preview_and_delete(test_env):
    """Verifies authenticated download, preview, tenant isolation, and delete authorization."""
    tokens = test_env["tokens"]
    headers_mem1 = {"Authorization": f"Bearer {tokens['member1']}"}
    headers_mem2 = {"Authorization": f"Bearer {tokens['member2']}"}
    headers_owner = {"Authorization": f"Bearer {tokens['owner']}"}
    headers_org2 = {"Authorization": f"Bearer {tokens['org2_user']}"}
    disc_id = test_env["discussion_id"]

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Upload attachment to existing discussion
        files = {"file": ("network.png", VALID_PNG_BYTES, "image/png")}
        upload_res = await client.post(
            f"/api/v1/discussions/{disc_id}/attachments",
            files=files,
            headers=headers_mem1
        )
        assert upload_res.status_code == 201
        att_id = upload_res.json()["id"]

        # 1. Download by same-organization user (member2) succeeds
        dl_res = await client.get(
            f"/api/v1/discussions/{disc_id}/attachments/{att_id}/download",
            headers=headers_mem2
        )
        assert dl_res.status_code == 200
        assert "attachment" in dl_res.headers.get("content-disposition", "")
        assert dl_res.headers.get("x-content-type-options") == "nosniff"

        # 2. Download by different-organization user (org2_user) returns 404 (tenant isolation)
        dl_org2_res = await client.get(
            f"/api/v1/discussions/{disc_id}/attachments/{att_id}/download",
            headers=headers_org2
        )
        assert dl_org2_res.status_code == 404

        # 3. Preview by authorized user succeeds with inline disposition
        prev_res = await client.get(
            f"/api/v1/discussions/{disc_id}/attachments/{att_id}/preview",
            headers=headers_mem1
        )
        assert prev_res.status_code == 200
        assert "inline" in prev_res.headers.get("content-disposition", "")

        # 4. Deletion: member2 (not uploader, not owner/admin) is forbidden (403)
        del_fail = await client.delete(
            f"/api/v1/discussions/{disc_id}/attachments/{att_id}",
            headers=headers_mem2
        )
        assert del_fail.status_code == 403

        # 5. Deletion: org owner can delete (200)
        del_ok = await client.delete(
            f"/api/v1/discussions/{disc_id}/attachments/{att_id}",
            headers=headers_owner
        )
        assert del_ok.status_code == 200

        # After deletion, download returns 404
        dl_after = await client.get(
            f"/api/v1/discussions/{disc_id}/attachments/{att_id}/download",
            headers=headers_mem1
        )
        assert dl_after.status_code == 404
