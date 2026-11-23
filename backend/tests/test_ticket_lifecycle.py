import os
import uuid
import json
import pytest
import httpx
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool

from backend.app.main import app
from backend.app.database import Base, get_db
from backend.app.models import (
    User,
    Organization,
    Project,
    Ticket,
    TicketComment,
    TicketHandoff,
    Membership,
    AuditLog,
    Notification,
)
from backend.app.core.config import settings
from backend.app.core.security import create_access_token


def _utc_now():
    return datetime.now(timezone.utc)


@pytest.fixture
def anyio_backend():
    return "asyncio"


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

    # Seed test entities
    org1_id = f"org_{uuid.uuid4().hex[:8]}"
    org2_id = f"org_{uuid.uuid4().hex[:8]}"
    u_owner_id = f"usr_owner_{uuid.uuid4().hex[:8]}"
    u_member1_id = f"usr_m1_{uuid.uuid4().hex[:8]}"
    u_member2_id = f"usr_m2_{uuid.uuid4().hex[:8]}"
    u_suspended_id = f"usr_susp_{uuid.uuid4().hex[:8]}"
    u_org2_id = f"usr_org2_{uuid.uuid4().hex[:8]}"
    proj1_id = f"proj_{uuid.uuid4().hex[:8]}"
    proj2_id = f"proj2_{uuid.uuid4().hex[:8]}"

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
        user_suspended = User(
            id=u_suspended_id,
            email="suspended@alpha.com",
            hashed_password="pw",
            full_name="Suspended Member",
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
        session.add_all([user_owner, user_member1, user_member2, user_suspended, user_org2])

        # Memberships
        m_owner = Membership(id=str(uuid.uuid4()), user_id=u_owner_id, organization_id=org1_id, role="owner", status="active")
        m_mem1 = Membership(id=str(uuid.uuid4()), user_id=u_member1_id, organization_id=org1_id, role="member", status="active")
        m_mem2 = Membership(id=str(uuid.uuid4()), user_id=u_member2_id, organization_id=org1_id, role="member", status="active")
        m_susp = Membership(id=str(uuid.uuid4()), user_id=u_suspended_id, organization_id=org1_id, role="member", status="suspended")
        m_org2 = Membership(id=str(uuid.uuid4()), user_id=u_org2_id, organization_id=org2_id, role="member", status="active")
        session.add_all([m_owner, m_mem1, m_mem2, m_susp, m_org2])

        proj1 = Project(id=proj1_id, name="Project Alpha", organization_id=org1_id, created_at=_utc_now())
        proj2 = Project(id=proj2_id, name="Project Beta", organization_id=org2_id, created_at=_utc_now())
        session.add_all([proj1, proj2])

        await session.commit()

    tokens = {
        "owner": create_access_token(u_owner_id, role="owner", org_id=org1_id),
        "member1": create_access_token(u_member1_id, role="member", org_id=org1_id),
        "member2": create_access_token(u_member2_id, role="member", org_id=org1_id),
        "suspended": create_access_token(u_suspended_id, role="member", org_id=org1_id),
        "org2_user": create_access_token(u_org2_id, role="member", org_id=org2_id),
    }

    env_data = {
        "engine": engine,
        "session_factory": session_factory,
        "org1_id": org1_id,
        "org2_id": org2_id,
        "u_owner_id": u_owner_id,
        "u_member1_id": u_member1_id,
        "u_member2_id": u_member2_id,
        "u_suspended_id": u_suspended_id,
        "u_org2_id": u_org2_id,
        "proj1_id": proj1_id,
        "proj2_id": proj2_id,
        "tokens": tokens,
    }

    yield env_data

    app.dependency_overrides.clear()
    await engine.dispose()


async def _create_test_ticket(session_factory, project_id, reporter_id, assignee_id=None, status="open", title="Test Ticket", version=0):
    ticket_id = f"tkt_{uuid.uuid4().hex[:8]}"
    now = _utc_now()
    async with session_factory() as session:
        tkt = Ticket(
            id=ticket_id,
            ticket_number=f"TKT-{uuid.uuid4().hex[:4].upper()}",
            title=title,
            description="Test ticket description",
            severity="major",
            status=status,
            project_id=project_id,
            reporter_id=reporter_id,
            assignee_id=assignee_id,
            version=version,
            created_at=now,
            updated_at=now,
        )
        session.add(tkt)
        await session.commit()
    return ticket_id


@pytest.mark.anyio
async def test_accept_ticket_flow(test_env):
    """
    1. open -> in_progress:
       - Assignee accepts.
       - Any member claims unassigned ticket.
       - Non-assignee cannot accept assigned open ticket (403).
       - Cannot accept non-open ticket (409).
    """
    tokens = test_env["tokens"]
    sf = test_env["session_factory"]

    # Ticket 1: assigned to member1, in open status
    tkt1 = await _create_test_ticket(sf, test_env["proj1_id"], test_env["u_owner_id"], assignee_id=test_env["u_member1_id"], status="open")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Non-assignee (member2) tries to accept -> 403
        r_forbidden = await client.post(
            f"/api/v1/tickets/{tkt1}/accept",
            headers={"Authorization": f"Bearer {tokens['member2']}"}
        )
        assert r_forbidden.status_code == 403
        assert r_forbidden.json()["error"]["code"] == "not_assignee"

        # Assignee (member1) accepts -> 200, status becomes in_progress
        r_accept = await client.post(
            f"/api/v1/tickets/{tkt1}/accept",
            headers={"Authorization": f"Bearer {tokens['member1']}"}
        )
        assert r_accept.status_code == 200
        assert r_accept.json()["status"] == "in_progress"

        # Accepting again (now in_progress) -> 409
        r_again = await client.post(
            f"/api/v1/tickets/{tkt1}/accept",
            headers={"Authorization": f"Bearer {tokens['member1']}"}
        )
        assert r_again.status_code == 409
        assert r_again.json()["error"]["code"] == "invalid_transition"

    # Ticket 2: unassigned open ticket -> member2 claims it
    tkt2 = await _create_test_ticket(sf, test_env["proj1_id"], test_env["u_owner_id"], assignee_id=None, status="open")
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r_claim = await client.post(
            f"/api/v1/tickets/{tkt2}/accept",
            headers={"Authorization": f"Bearer {tokens['member2']}"}
        )
        assert r_claim.status_code == 200
        assert r_claim.json()["status"] == "in_progress"
        assert r_claim.json()["assignee_id"] == test_env["u_member2_id"]


@pytest.mark.anyio
async def test_pass_ticket_flow(test_env):
    """
    2. in_progress -> passed:
       - Only current assignee.
       - To another active member of same org (not self, not suspended).
       - Required message (10 to 1000 characters).
       - Writes ticket_handoffs row (kind 'pass') and ticket_comment.
    """
    tokens = test_env["tokens"]
    sf = test_env["session_factory"]

    # Start in_progress assigned to member1
    tkt = await _create_test_ticket(sf, test_env["proj1_id"], test_env["u_owner_id"], assignee_id=test_env["u_member1_id"], status="in_progress")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Non-assignee (owner or member2) tries to pass -> 403
        r_non_assignee = await client.post(
            f"/api/v1/tickets/{tkt}/pass",
            headers={"Authorization": f"Bearer {tokens['member2']}"},
            json={"to_user_id": test_env["u_owner_id"], "message": "Here is the handover notes for triage"}
        )
        assert r_non_assignee.status_code == 403
        assert r_non_assignee.json()["error"]["code"] == "not_assignee"

        # Pass to self -> 409
        r_self = await client.post(
            f"/api/v1/tickets/{tkt}/pass",
            headers={"Authorization": f"Bearer {tokens['member1']}"},
            json={"to_user_id": test_env["u_member1_id"], "message": "Handing off ticket to myself now"}
        )
        assert r_self.status_code == 409
        assert r_self.json()["error"]["code"] == "cannot_pass_to_self"

        # Pass to user in another org -> 403
        r_other_org = await client.post(
            f"/api/v1/tickets/{tkt}/pass",
            headers={"Authorization": f"Bearer {tokens['member1']}"},
            json={"to_user_id": test_env["u_org2_id"], "message": "Handing off across workspace boundaries"}
        )
        assert r_other_org.status_code == 403

        # Pass to suspended user -> 403
        r_suspended = await client.post(
            f"/api/v1/tickets/{tkt}/pass",
            headers={"Authorization": f"Bearer {tokens['member1']}"},
            json={"to_user_id": test_env["u_suspended_id"], "message": "Handing off to a suspended team member"}
        )
        assert r_suspended.status_code == 403
        assert r_suspended.json()["error"]["code"] == "target_user_suspended"

        # Missing or too short message (< 10 chars) -> 422
        r_short = await client.post(
            f"/api/v1/tickets/{tkt}/pass",
            headers={"Authorization": f"Bearer {tokens['member1']}"},
            json={"to_user_id": test_env["u_member2_id"], "message": "short"}
        )
        assert r_short.status_code == 422

        # Valid pass to member2
        valid_msg = "Completed primary network trace analysis. Passing to you for application inspection."
        r_pass = await client.post(
            f"/api/v1/tickets/{tkt}/pass",
            headers={"Authorization": f"Bearer {tokens['member1']}"},
            json={"to_user_id": test_env["u_member2_id"], "message": valid_msg}
        )
        assert r_pass.status_code == 200
        assert r_pass.json()["status"] == "passed"
        assert r_pass.json()["assignee_id"] == test_env["u_member2_id"]

    # Verify handoff row and comment were written to DB
    async with sf() as session:
        res_h = await session.execute(select(TicketHandoff).where(TicketHandoff.ticket_id == tkt))
        handoffs = res_h.scalars().all()
        assert len(handoffs) == 1
        assert handoffs[0].kind == "pass"
        assert handoffs[0].from_user_id == test_env["u_member1_id"]
        assert handoffs[0].to_user_id == test_env["u_member2_id"]
        assert handoffs[0].message == valid_msg

        res_c = await session.execute(select(TicketComment).where(TicketComment.ticket_id == tkt))
        comments = res_c.scalars().all()
        assert len(comments) == 1
        assert comments[0].author_id == test_env["u_member1_id"]
        assert comments[0].content == valid_msg


@pytest.mark.anyio
async def test_resume_and_hold_flow(test_env):
    """
    3. passed -> in_progress: only person it was passed to.
    4. in_progress <-> on_hold: only assignee, with required reason.
    """
    tokens = test_env["tokens"]
    sf = test_env["session_factory"]

    # Ticket passed to member2
    tkt = await _create_test_ticket(sf, test_env["proj1_id"], test_env["u_owner_id"], assignee_id=test_env["u_member2_id"], status="passed")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Non-recipient (member1 or owner) tries to resume -> 403
        r_non_recip = await client.post(
            f"/api/v1/tickets/{tkt}/resume",
            headers={"Authorization": f"Bearer {tokens['member1']}"}
        )
        assert r_non_recip.status_code == 403
        assert r_non_recip.json()["error"]["code"] == "not_assignee"

        # Recipient (member2) resumes -> 200, status becomes in_progress
        r_res = await client.post(
            f"/api/v1/tickets/{tkt}/resume",
            headers={"Authorization": f"Bearer {tokens['member2']}"}
        )
        assert r_res.status_code == 200
        assert r_res.json()["status"] == "in_progress"

        # Hold: missing reason -> 422
        r_hold_no_reason = await client.post(
            f"/api/v1/tickets/{tkt}/hold",
            headers={"Authorization": f"Bearer {tokens['member2']}"},
            json={"reason": ""}
        )
        assert r_hold_no_reason.status_code == 422

        # Hold: non-assignee (member1) -> 403
        r_hold_forbidden = await client.post(
            f"/api/v1/tickets/{tkt}/hold",
            headers={"Authorization": f"Bearer {tokens['member1']}"},
            json={"reason": "Waiting for vendor hardware"}
        )
        assert r_hold_forbidden.status_code == 403

        # Hold: valid by member2 -> status becomes on_hold
        r_hold = await client.post(
            f"/api/v1/tickets/{tkt}/hold",
            headers={"Authorization": f"Bearer {tokens['member2']}"},
            json={"reason": "Waiting on upstream vendor response for packet drop issue"}
        )
        assert r_hold.status_code == 200
        assert r_hold.json()["status"] == "on_hold"

        # Resume from on_hold: assignee (member2) resumes -> status becomes in_progress
        r_resume_hold = await client.post(
            f"/api/v1/tickets/{tkt}/resume",
            headers={"Authorization": f"Bearer {tokens['member2']}"}
        )
        assert r_resume_hold.status_code == 200
        assert r_resume_hold.json()["status"] == "in_progress"


@pytest.mark.anyio
async def test_close_and_reopen_flow(test_env):
    """
    5. CLOSING: only organization owner, and only when:
       (a) ticket is passed to owner (passed + owner is assignee), or
       (b) owner is assignee in in_progress.
       (7) owner closing existing resolved ticket.
       Closing from any other state or by anyone else returns 409 or 403.
       Owner may reopen (closed -> open) with required message.
    """
    tokens = test_env["tokens"]
    sf = test_env["session_factory"]
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Case 1: Open ticket -> owner tries to close -> 409
        tkt_open = await _create_test_ticket(sf, test_env["proj1_id"], test_env["u_member1_id"], assignee_id=test_env["u_owner_id"], status="open")
        r_close_open = await client.post(
            f"/api/v1/tickets/{tkt_open}/close",
            headers={"Authorization": f"Bearer {tokens['owner']}"},
            json={"message": "Closing open ticket"}
        )
        assert r_close_open.status_code == 409
        assert r_close_open.json()["error"]["code"] == "invalid_closing_state"

        # Case 2: In-progress ticket assigned to member1 -> owner tries to close -> 409 (not assigned to owner)
        tkt_inp_m1 = await _create_test_ticket(sf, test_env["proj1_id"], test_env["u_owner_id"], assignee_id=test_env["u_member1_id"], status="in_progress")
        r_close_inp_m1 = await client.post(
            f"/api/v1/tickets/{tkt_inp_m1}/close",
            headers={"Authorization": f"Bearer {tokens['owner']}"},
            json={"message": "Closing ticket"}
        )
        assert r_close_inp_m1.status_code == 409
        assert r_close_inp_m1.json()["error"]["code"] == "owner_not_assignee"

        # Case 3: Passed ticket, but passed to member2 (not owner) -> owner tries to close -> 409
        tkt_pass_m2 = await _create_test_ticket(sf, test_env["proj1_id"], test_env["u_owner_id"], assignee_id=test_env["u_member2_id"], status="passed")
        r_close_pass_m2 = await client.post(
            f"/api/v1/tickets/{tkt_pass_m2}/close",
            headers={"Authorization": f"Bearer {tokens['owner']}"},
            json={"message": "Closing ticket"}
        )
        assert r_close_pass_m2.status_code == 409
        assert r_close_pass_m2.json()["error"]["code"] == "not_passed_to_owner"

        # Case 4: Non-owner (member1) tries to close ticket passed to owner -> 403
        tkt_pass_owner = await _create_test_ticket(sf, test_env["proj1_id"], test_env["u_member1_id"], assignee_id=test_env["u_owner_id"], status="passed")
        r_close_non_owner = await client.post(
            f"/api/v1/tickets/{tkt_pass_owner}/close",
            headers={"Authorization": f"Bearer {tokens['member1']}"},
            json={"message": "Non-owner closing"}
        )
        assert r_close_non_owner.status_code == 403
        assert r_close_non_owner.json()["error"]["code"] == "owner_required"

        # Case 5: Owner closing ticket passed to owner -> 200 (SUCCESS)
        r_close_success = await client.post(
            f"/api/v1/tickets/{tkt_pass_owner}/close",
            headers={"Authorization": f"Bearer {tokens['owner']}"},
            json={"message": "Reviewed operational resolution and confirmed closed"}
        )
        assert r_close_success.status_code == 200
        assert r_close_success.json()["status"] == "closed"

        # Case 6: Owner closing in_progress ticket where owner is assignee -> 200 (SUCCESS)
        tkt_inp_owner = await _create_test_ticket(sf, test_env["proj1_id"], test_env["u_member1_id"], assignee_id=test_env["u_owner_id"], status="in_progress")
        r_close_inp_owner = await client.post(
            f"/api/v1/tickets/{tkt_inp_owner}/close",
            headers={"Authorization": f"Bearer {tokens['owner']}"},
            json={"message": "Owner resolved in progress and closed"}
        )
        assert r_close_inp_owner.status_code == 200
        assert r_close_inp_owner.json()["status"] == "closed"

        # Case 7: Owner closing legacy resolved ticket -> 200 (SUCCESS)
        tkt_resolved = await _create_test_ticket(sf, test_env["proj1_id"], test_env["u_member1_id"], assignee_id=test_env["u_member2_id"], status="resolved")
        r_close_resolved = await client.post(
            f"/api/v1/tickets/{tkt_resolved}/close",
            headers={"Authorization": f"Bearer {tokens['owner']}"},
            json={"message": "Verified resolved ticket closed"}
        )
        assert r_close_resolved.status_code == 200
        assert r_close_resolved.json()["status"] == "closed"

        # Reopen: Non-owner trying to reopen -> 403
        r_reopen_non_owner = await client.post(
            f"/api/v1/tickets/{tkt_pass_owner}/reopen",
            headers={"Authorization": f"Bearer {tokens['member1']}"},
            json={"message": "Reopening defect"}
        )
        assert r_reopen_non_owner.status_code == 403

        # Reopen: Missing message -> 422
        r_reopen_no_msg = await client.post(
            f"/api/v1/tickets/{tkt_pass_owner}/reopen",
            headers={"Authorization": f"Bearer {tokens['owner']}"},
            json={"message": ""}
        )
        assert r_reopen_no_msg.status_code == 422

        # Reopen: Owner reopening closed ticket with message -> 200, status becomes open
        r_reopen_success = await client.post(
            f"/api/v1/tickets/{tkt_pass_owner}/reopen",
            headers={"Authorization": f"Bearer {tokens['owner']}"},
            json={"message": "Customer reported recurrent regression, reopening for inspection."}
        )
        assert r_reopen_success.status_code == 200
        assert r_reopen_success.json()["status"] == "open"


@pytest.mark.anyio
async def test_owner_reassign_flow(test_env):
    """
    6. OWNER REASSIGN:
       Owner can reassign any non-closed ticket with required message (10 to 1000 chars).
       Status becomes passed, handoff row with kind 'reassign' written, comment posted.
       Non-owner cannot reassign (403).
       Cannot reassign closed ticket (409).
    """
    tokens = test_env["tokens"]
    sf = test_env["session_factory"]
    transport = httpx.ASGITransport(app=app)

    # In-progress ticket held by member1
    tkt = await _create_test_ticket(sf, test_env["proj1_id"], test_env["u_owner_id"], assignee_id=test_env["u_member1_id"], status="in_progress")

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Non-owner (member1 or member2) tries to reassign -> 403
        r_non_owner = await client.post(
            f"/api/v1/tickets/{tkt}/reassign",
            headers={"Authorization": f"Bearer {tokens['member1']}"},
            json={"assignee_id": test_env["u_member2_id"], "message": "Owner intervention required here"}
        )
        assert r_non_owner.status_code == 403
        assert r_non_owner.json()["error"]["code"] == "owner_required"

        # Missing message -> 422
        r_short = await client.post(
            f"/api/v1/tickets/{tkt}/reassign",
            headers={"Authorization": f"Bearer {tokens['owner']}"},
            json={"assignee_id": test_env["u_member2_id"], "message": "short"}
        )
        assert r_short.status_code == 422

        # Owner reassigns to member2 -> 200, status becomes passed
        reassign_msg = "Reassigning to operational lead for critical customer escalation."
        r_reassign = await client.post(
            f"/api/v1/tickets/{tkt}/reassign",
            headers={"Authorization": f"Bearer {tokens['owner']}"},
            json={"assignee_id": test_env["u_member2_id"], "message": reassign_msg}
        )
        assert r_reassign.status_code == 200
        assert r_reassign.json()["status"] == "passed"
        assert r_reassign.json()["assignee_id"] == test_env["u_member2_id"]

    # Verify handoff row has kind 'reassign'
    async with sf() as session:
        res_h = await session.execute(select(TicketHandoff).where(TicketHandoff.ticket_id == tkt))
        handoffs = res_h.scalars().all()
        assert len(handoffs) == 1
        assert handoffs[0].kind == "reassign"
        assert handoffs[0].from_user_id == test_env["u_owner_id"]
        assert handoffs[0].to_user_id == test_env["u_member2_id"]


@pytest.mark.anyio
async def test_resolved_and_in_progress_back_to_open_forbidden(test_env):
    """
    7. Status 'resolved' is no longer reachable by any transition.
    Rule: Nobody can move in_progress back to open.
    """
    tokens = test_env["tokens"]
    sf = test_env["session_factory"]
    transport = httpx.ASGITransport(app=app)

    tkt = await _create_test_ticket(sf, test_env["proj1_id"], test_env["u_owner_id"], assignee_id=test_env["u_member1_id"], status="in_progress")

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Try moving to resolved via PATCH status -> 409
        r_resolved = await client.patch(
            f"/api/v1/tickets/{tkt}/status",
            headers={"Authorization": f"Bearer {tokens['owner']}"},
            json={"status": "resolved"}
        )
        assert r_resolved.status_code == 409
        assert r_resolved.json()["error"]["code"] == "invalid_transition"

        # Try moving in_progress back to open -> 409
        r_back_to_open = await client.patch(
            f"/api/v1/tickets/{tkt}/status",
            headers={"Authorization": f"Bearer {tokens['owner']}"},
            json={"status": "open"}
        )
        assert r_back_to_open.status_code == 409
        assert r_back_to_open.json()["error"]["code"] == "invalid_transition"


@pytest.mark.anyio
async def test_concurrency_and_org_isolation(test_env):
    """
    - Concurrency: version mismatch returns 409.
    - Organization isolation: user from Org2 cannot access or transition Org1 ticket.
    """
    tokens = test_env["tokens"]
    sf = test_env["session_factory"]
    transport = httpx.ASGITransport(app=app)

    tkt = await _create_test_ticket(sf, test_env["proj1_id"], test_env["u_owner_id"], assignee_id=test_env["u_member1_id"], status="open", version=2)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Outdated version in accept -> 409
        r_concurrency = await client.post(
            f"/api/v1/tickets/{tkt}/accept",
            headers={"Authorization": f"Bearer {tokens['member1']}"},
            json={"version": 1}  # actual is 2
        )
        assert r_concurrency.status_code == 409
        assert r_concurrency.json()["error"]["code"] == "concurrency_conflict"

        # User from Org2 accessing Org1 ticket -> 404
        r_isolation = await client.post(
            f"/api/v1/tickets/{tkt}/accept",
            headers={"Authorization": f"Bearer {tokens['org2_user']}"}
        )
        assert r_isolation.status_code == 404
