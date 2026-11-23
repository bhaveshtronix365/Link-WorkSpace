import os
import uuid
import json
import time
import logging
from pathlib import Path
from typing import List, Optional, Dict
from datetime import datetime, timezone
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
    UploadFile,
    File,
    status,
)
from sqlalchemy import select, and_, or_, desc, func, update
from sqlalchemy.orm import selectinload, noload
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from backend.app.core.config import settings
from backend.app.database import get_db
from backend.app.dependencies import get_current_active_user
from backend.app.services.sla import is_active_sla_breach, check_ticket_sla_breach
from backend.app.models import (
    Ticket,
    Project,
    User,
    Organization,
    ProjectMember,
    Membership,
    TicketComment,
    TicketAttachment,
    TicketActivity,
    TicketHandoff,
    AuditLog,
    Notification,
    Task,
    TaskActivity,
)
from backend.app.enums import TicketStatus
from fastapi.responses import FileResponse
from backend.app.schemas import (
    UserRead,
    TicketCreate,
    TicketUpdate,
    TicketStatusChangeRequest,
    TicketSeverityChangeRequest,
    TicketReassignRequest,
    TicketDeadlineChangeRequest,
    TicketReopenRequest,
    TicketCloseRequest,
    TicketAcceptRequest,
    TicketPassRequest,
    TicketHoldRequest,
    TicketResumeRequest,
    TicketOwnerReassignRequest,
    TicketRead,
    TicketStats,
    CriticalIssuesResponse,
    CriticalIssuesKPI,
    TicketCommentCreate,
    TicketCommentRead,
    TicketAttachmentRead,
    TicketActivityRead,
    TicketHandoffRead,
)
from backend.app.core.websockets import ws_manager, authenticate_ws

router = APIRouter()
logger = logging.getLogger(__name__)

ALLOWED_ATTACHMENT_EXTENSIONS = set(getattr(settings, "ATTACHMENT_ALLOWED_EXTENSIONS", [
    ".png", ".jpg", ".jpeg", ".gif", ".webp",
    ".pdf", ".txt", ".json", ".csv", ".log"
]))


@router.websocket("/ws/{workspace_id}")
async def tickets_websocket_endpoint(
    websocket: WebSocket,
    workspace_id: str,
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db)
):
    """Authenticated and workspace-scoped WebSocket endpoint for real-time ticket events."""
    user = await authenticate_ws(websocket, token, db, required_workspace_id=workspace_id)
    if not user:
        return

    await ws_manager.connect(websocket, workspace_id)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, workspace_id)


# =========================================================================
# STATIC ROUTES (MUST BE DECLARED BEFORE /{ticket_id})
# =========================================================================

@router.get("/critical", response_model=CriticalIssuesResponse)
async def get_critical_priority_issues(
    project_id: Optional[str] = Query(None, description="Optional project filter"),
    assignee_id: Optional[str] = Query(None, description="Filter by assignee user ID"),
    sla_status: Optional[str] = Query(None, description="Filter by SLA status: breached, approaching, stable"),
    sort_by: Optional[str] = Query("severity", description="Sort by: severity, deadline, updated"),
    sort_order: Optional[str] = Query("desc", description="Sort order: asc, desc"),
    search: Optional[str] = Query(None, description="Search by title or ticket number"),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """Screen 69: Live Incident Queue with KPI Impact summary and multi-tier filtering."""
    query = (
        select(Ticket)
        .options(
            selectinload(Ticket.assignee),
            selectinload(Ticket.reporter),
            selectinload(Ticket.project),
            selectinload(Ticket.comments),
            selectinload(Ticket.attachments),
            selectinload(Ticket.activities),
        )
        .join(Ticket.project)
        .where(Project.organization_id == current_user.organization_id)
    )

    if project_id and project_id.lower() != "all":
        query = query.where(Ticket.project_id == project_id)
    if assignee_id and assignee_id.lower() != "all":
        query = query.where(Ticket.assignee_id == assignee_id)
    if search:
        s = f"%{search.strip()}%"
        query = query.where(Ticket.title.ilike(s) | Ticket.ticket_number.ilike(s))

    if sort_by == "deadline":
        order_col = Ticket.deadline.asc() if sort_order == "asc" else Ticket.deadline.desc()
        query = query.order_by(order_col.nullslast(), desc(Ticket.created_at))
    elif sort_by == "updated":
        order_col = Ticket.updated_at.asc() if sort_order == "asc" else Ticket.updated_at.desc()
        query = query.order_by(order_col, desc(Ticket.created_at))
    else:
        query = query.order_by(desc(Ticket.created_at))

    res = await db.execute(query)
    all_tickets = res.scalars().all()

    now = datetime.now(timezone.utc)
    blockers = []
    critical = []
    major = []

    # Active SLA breach defined once: deadline in past and status not resolved or closed
    sla_breached_count = sum(1 for t in all_tickets if is_active_sla_breach(t, now))

    for t in all_tickets:
        is_breached = is_active_sla_breach(t, now)
        is_approaching = False

        if (t.status or "").lower() not in ("resolved", "closed") and t.deadline and not is_breached:
            t_deadline = t.deadline if t.deadline.tzinfo else t.deadline.replace(tzinfo=timezone.utc)
            diff_sec = (t_deadline - now).total_seconds()
            if 0 <= diff_sec < 3600:
                is_approaching = True

        # Check sla_status filter if specified
        if sla_status and sla_status.lower() != "all":
            flt = sla_status.lower()
            if flt == "breached" and not is_breached:
                continue
            elif flt == "approaching" and not is_approaching:
                continue
            elif flt == "stable" and (is_breached or is_approaching):
                continue

        sev = (t.severity or "").lower()
        if sev == "blocker":
            blockers.append(TicketRead.model_validate(t))
        elif sev == "critical":
            critical.append(TicketRead.model_validate(t))
        elif sev == "major":
            major.append(TicketRead.model_validate(t))

    kpi = CriticalIssuesKPI(
        blockers_active=len([t for t in blockers if t.status not in ("resolved", "closed")]),
        critical_incidents=len([t for t in critical if t.status not in ("resolved", "closed")]),
        major_issues=len([t for t in major if t.status not in ("resolved", "closed")]),
        sla_breached=sla_breached_count,
        sync_interval_sec=5,
    )

    return CriticalIssuesResponse(
        kpi=kpi,
        blockers=blockers,
        critical=critical,
        major=major,
    )


@router.get("/stats", response_model=TicketStats)
async def get_ticket_stats(
    project_id: Optional[str] = Query(None, description="Optional project filter"),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """Screen 61: Summary counts for dashboard stat cards."""
    query = (
        select(Ticket)
        .options(
            noload(Ticket.comments),
            noload(Ticket.attachments),
            noload(Ticket.activities),
            noload(Ticket.handoffs),
            noload(Ticket.assignee),
            noload(Ticket.reporter),
        )
        .join(Ticket.project)
        .where(Project.organization_id == current_user.organization_id)
    )
    if project_id and project_id.lower() != "all":
        query = query.where(Ticket.project_id == project_id)

    res = await db.execute(query)
    tickets = res.scalars().all()

    now = datetime.now(timezone.utc)
    total = len(tickets)
    open_count = sum(1 for t in tickets if (t.status or "").lower() == "open")
    wip_count = sum(1 for t in tickets if (t.status or "").lower() in ("wip", "in_progress"))
    passed_count = sum(1 for t in tickets if (t.status or "").lower() == "passed")
    on_hold_count = sum(1 for t in tickets if (t.status or "").lower() == "on_hold")
    resolved_count = sum(1 for t in tickets if (t.status or "").lower() == "resolved")
    closed_count = sum(1 for t in tickets if (t.status or "").lower() == "closed")
    sla_breached = sum(1 for t in tickets if is_active_sla_breach(t, now))

    return TicketStats(
        total=total,
        open=open_count,
        wip=wip_count,
        passed=passed_count,
        on_hold=on_hold_count,
        resolved=resolved_count,
        closed=closed_count,
        sla_breached=sla_breached,
    )


def normalize_ticket_status(val: Optional[str]) -> Optional[str]:
    if not val or val.lower() == "all":
        return None
    normalized = val.lower().strip()
    if normalized in ("wip", "in_progress", "in-progress", "inprogress"):
        return "wip"
    if normalized in ("on_hold", "on-hold", "onhold"):
        return "on_hold"
    return normalized


def normalize_ticket_severity(val: Optional[str]) -> Optional[str]:
    if not val or val.lower() == "all":
        return None
    return val.lower().strip()


async def get_filtered_tickets_query(
    db: AsyncSession,
    organization_id: str,
    project_id: Optional[str] = None,
    task_id: Optional[str] = None,
    severity: Optional[str] = None,
    status: Optional[str] = None,
    assignee_id: Optional[str] = None,
    search: Optional[str] = None,
    sla: Optional[str] = None,
    sla_status: Optional[str] = None,
) -> List[Ticket]:
    """
    Unified filtering logic for List, Board, and Priority ticket endpoints.
    Strictly scoped to the user's organization.
    Eagerly loads all nested relationships required by TicketRead.
    """
    query = (
        select(Ticket)
        .options(
            selectinload(Ticket.assignee),
            selectinload(Ticket.reporter),
            selectinload(Ticket.project),
            selectinload(Ticket.task).selectinload(Task.assignee),
            selectinload(Ticket.comments).selectinload(TicketComment.author),
            selectinload(Ticket.comments).selectinload(TicketComment.attachments).selectinload(TicketAttachment.uploader),
            selectinload(Ticket.attachments).selectinload(TicketAttachment.uploader),
            selectinload(Ticket.activities).selectinload(TicketActivity.actor),
            selectinload(Ticket.handoffs).selectinload(TicketHandoff.from_user),
            selectinload(Ticket.handoffs).selectinload(TicketHandoff.to_user),
        )
        .join(Ticket.project)
        .where(Project.organization_id == organization_id)
    )

    if project_id and project_id.lower() != "all":
        query = query.where(Ticket.project_id == project_id)

    if task_id and task_id.lower() != "all":
        query = query.where(Ticket.task_id == task_id)

    norm_sev = normalize_ticket_severity(severity)
    if norm_sev:
        query = query.where(Ticket.severity == norm_sev)

    norm_status = normalize_ticket_status(status)
    if norm_status == "active":
        query = query.where(Ticket.status.notin_(["resolved", "closed"]))
    elif norm_status in ("wip", "in_progress"):
        query = query.where(Ticket.status.in_(["wip", "in_progress", "inprogress", "in-progress"]))
    elif norm_status in ("on_hold",):
        query = query.where(Ticket.status.in_(["on_hold", "onhold", "on-hold"]))
    elif norm_status:
        query = query.where(func.lower(Ticket.status) == norm_status)

    if assignee_id and assignee_id.lower() != "all":
        query = query.where(Ticket.assignee_id == assignee_id)

    if search and search.strip():
        s = f"%{search.strip()}%"
        query = query.where(Ticket.title.ilike(s) | Ticket.ticket_number.ilike(s))

    query = query.order_by(desc(Ticket.created_at))
    res = await db.execute(query)
    tickets = res.scalars().all()

    effective_sla = (sla or sla_status or "").lower()
    if effective_sla == "breached":
        now = datetime.now(timezone.utc)
        tickets = [t for t in tickets if is_active_sla_breach(t, now)]

    return tickets


@router.get("/board")
async def get_ticket_board(
    project_id: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None),
    assignee_id: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    sla: Optional[str] = Query(None),
    sla_status: Optional[str] = Query(None),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """Screen 62: Tickets grouped by status columns for the Kanban board."""
    tickets = await get_filtered_tickets_query(
        db=db,
        organization_id=current_user.organization_id,
        project_id=project_id,
        severity=severity,
        status=status or status_filter,
        assignee_id=assignee_id,
        search=search,
        sla=sla,
        sla_status=sla_status,
    )

    has_resolved = any((t.status or "").lower() == "resolved" for t in tickets)
    board: Dict[str, List] = {
        "open": [],
        "wip": [],
        "passed": [],
        "on_hold": [],
    }
    if has_resolved:
        board["resolved"] = []
    board["closed"] = []

    for t in tickets:
        status_key = (t.status or "").lower()
        if status_key in ("in_progress", "wip"):
            board["wip"].append(TicketRead.model_validate(t))
        elif status_key in board:
            board[status_key].append(TicketRead.model_validate(t))
        else:
            board["open"].append(TicketRead.model_validate(t))

    return board


@router.get("/priority")
async def get_ticket_priority(
    project_id: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None),
    assignee_id: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    sla: Optional[str] = Query(None),
    sla_status: Optional[str] = Query(None),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """Screen 63: Tickets grouped by priority swimlanes/sections."""
    tickets = await get_filtered_tickets_query(
        db=db,
        organization_id=current_user.organization_id,
        project_id=project_id,
        severity=severity,
        status=status or status_filter,
        assignee_id=assignee_id,
        search=search,
        sla=sla,
        sla_status=sla_status,
    )

    priority_groups: Dict[str, List] = {
        "blocker": [],
        "critical": [],
        "major": [],
        "minor": [],
    }

    for t in tickets:
        p_key = (t.severity or "").lower()
        if p_key in priority_groups:
            priority_groups[p_key].append(TicketRead.model_validate(t))
        else:
            priority_groups["major"].append(TicketRead.model_validate(t))

    return priority_groups


@router.get("", response_model=List[TicketRead])
@router.get("/", response_model=List[TicketRead])
async def list_tickets(
    project_id: Optional[str] = Query(None, description="Filter by project ID"),
    task_id: Optional[str] = Query(None, description="Filter by task ID"),
    severity: Optional[str] = Query(None, description="Filter by severity (blocker, critical, major, minor)"),
    status: Optional[str] = Query(None, description="Filter by status (open, wip, on_hold, resolved, closed)"),
    assignee_id: Optional[str] = Query(None, description="Filter by assignee ID"),
    search: Optional[str] = Query(None, description="Search in title or ticket number"),
    sla: Optional[str] = Query(None, description="Filter by SLA status: breached"),
    sla_status: Optional[str] = Query(None, description="Alias for sla filter: breached"),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """Screen 61: List tickets for dashboard table with filtering."""
    return await get_filtered_tickets_query(
        db=db,
        organization_id=current_user.organization_id,
        project_id=project_id,
        task_id=task_id,
        severity=severity,
        status=status,
        assignee_id=assignee_id,
        search=search,
        sla=sla,
        sla_status=sla_status,
    )


@router.post("", response_model=TicketRead, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=TicketRead, status_code=status.HTTP_201_CREATED)
async def create_ticket(
    ticket_in: TicketCreate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Screen 64: Create a new ticket with concurrency-safe ticket number generation:
    - Verifies project belongs to authenticated user's organization.
    - Protects against simultaneous generation races using database row locking where supported.
    - Uses controlled retry loop with transaction savepoint on unique constraint conflicts.
    - Computes next sequence without hardcoded offsets.
    """
    target_project_id = ticket_in.project_id
    task = None

    if ticket_in.task_id:
        t_res = await db.execute(
            select(Task)
            .options(selectinload(Task.assignee))
            .where(Task.id == ticket_in.task_id)
        )
        task = t_res.scalars().first()
        if not task:
            raise HTTPException(status_code=422, detail="Task not found")
        if task.organization_id != current_user.organization_id:
            raise HTTPException(status_code=422, detail="Task belongs to another organization")
        
        if ticket_in.project_id:
            if task.project_id and task.project_id != ticket_in.project_id:
                raise HTTPException(status_code=422, detail="Task belongs to a different project than the ticket")
            target_project_id = ticket_in.project_id
        else:
            # Set the ticket's project from the task if none is given
            target_project_id = task.project_id
            if not target_project_id:
                raise HTTPException(status_code=422, detail="Task is not associated with a project; project_id is required")
    else:
        if not target_project_id:
            raise HTTPException(status_code=422, detail="project_id is required")

    p_res = await db.execute(
        select(Project).where(
            Project.id == target_project_id,
            Project.organization_id == current_user.organization_id
        )
    )
    project = p_res.scalars().first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found or does not belong to your organization")

    # Validate assignee if provided
    if ticket_in.assignee_id and ticket_in.assignee_id != current_user.id:
        u_res = await db.execute(
            select(User).where(
                User.id == ticket_in.assignee_id,
                User.organization_id == current_user.organization_id
            )
        )
        if not u_res.scalars().first():
            raise HTTPException(status_code=400, detail="Assignee does not belong to your organization")

    now = datetime.now(timezone.utc)
    new_ticket = None
    MAX_RETRIES = 5

    for attempt in range(MAX_RETRIES):
        try:
            async with db.begin_nested():
                # Query highest existing ticket number globally to adhere to global unique constraint
                q = (
                    select(Ticket.ticket_number)
                    .order_by(desc(Ticket.created_at))
                    .limit(500)
                )
                try:
                    if db.bind and db.bind.dialect.name == "postgresql":
                        q = q.with_for_update(read=True)
                except Exception:
                    pass
                res_nums = await db.execute(q)
                all_nums = res_nums.scalars().all()
                max_num = 1000
                for n in all_nums:
                    if n and n.startswith("TKT-"):
                        try:
                            val = int(n.split("-")[1])
                            if val > max_num:
                                max_num = val
                        except (IndexError, ValueError):
                            pass
                candidate_num = max_num + 1 + attempt
                while True:
                    candidate_str = f"TKT-{candidate_num}"
                    existing = await db.execute(select(Ticket.id).where(Ticket.ticket_number == candidate_str))
                    if not existing.scalar():
                        tkt_num = candidate_str
                        break
                    candidate_num += 1

                new_ticket = Ticket(
                    ticket_number=tkt_num,
                    title=ticket_in.title.strip(),
                    description=ticket_in.description,
                    severity=(ticket_in.severity or "major").lower(),
                    status=(ticket_in.status or "open").lower(),
                    project_id=target_project_id,
                    task_id=task.id if task else None,
                    assignee_id=ticket_in.assignee_id,
                    reporter_id=ticket_in.reporter_id or current_user.id,
                    deadline=ticket_in.deadline,
                    first_response_at=ticket_in.first_response_at,
                    steps_to_reproduce=ticket_in.steps_to_reproduce,
                    diagnostic_trace=ticket_in.diagnostic_trace,
                    rca_trigger=ticket_in.rca_trigger,
                    rca_mitigation=ticket_in.rca_mitigation,
                    rca_action_items=ticket_in.rca_action_items,
                    created_at=now,
                    updated_at=now,
                )
                db.add(new_ticket)
                await db.flush()
                break
        except IntegrityError as exc:
            err_str = str(exc.orig).lower() if hasattr(exc, "orig") else str(exc).lower()
            if "ticket_number" in err_str or "unique constraint" in err_str or "unique" in err_str:
                if attempt == MAX_RETRIES - 1:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="Could not generate unique ticket number due to high concurrency. Please retry."
                    )
                continue
            raise

    # Optional initial comment
    if ticket_in.initial_comment and ticket_in.initial_comment.strip():
        comm = TicketComment(
            ticket_id=new_ticket.id,
            author_id=current_user.id,
            content=ticket_in.initial_comment.strip(),
            is_internal=True,
            created_at=now,
        )
        db.add(comm)

    # Initial activity record
    activity = TicketActivity(
        ticket_id=new_ticket.id,
        actor_id=current_user.id,
        action="ticket_created",
        old_value=None,
        new_value=f"Severity: {new_ticket.severity}, Status: {new_ticket.status}",
        created_at=now,
    )
    db.add(activity)

    if task:
        t_act_ticket = TicketActivity(
            ticket_id=new_ticket.id,
            actor_id=current_user.id,
            action="task_linked",
            old_value=None,
            new_value=task.task_number,
            created_at=now,
        )
        db.add(t_act_ticket)

        t_act_task = TaskActivity(
            task_id=task.id,
            organization_id=current_user.organization_id,
            actor_id=current_user.id,
            action="ticket_linked",
            field_name="task_id",
            old_value=None,
            new_value=new_ticket.ticket_number,
            meta={"ticket_id": new_ticket.id, "ticket_number": new_ticket.ticket_number, "ticket_title": new_ticket.title},
            created_at=now,
        )
        db.add(t_act_task)

    await db.commit()

    # Re-fetch full details
    res = await db.execute(
        select(Ticket).options(
            selectinload(Ticket.assignee),
            selectinload(Ticket.reporter),
            selectinload(Ticket.project),
            selectinload(Ticket.task).selectinload(Task.assignee),
            selectinload(Ticket.comments).selectinload(TicketComment.author),
            selectinload(Ticket.comments).selectinload(TicketComment.attachments).selectinload(TicketAttachment.uploader),
            selectinload(Ticket.attachments).selectinload(TicketAttachment.uploader),
            selectinload(Ticket.activities).selectinload(TicketActivity.actor),
            selectinload(Ticket.handoffs).selectinload(TicketHandoff.from_user),
            selectinload(Ticket.handoffs).selectinload(TicketHandoff.to_user),
        ).where(Ticket.id == new_ticket.id)
    )
    out_obj = res.scalars().first()

    # Broadcast real-time event to workspace
    workspace_id = project.organization_id if project and project.organization_id else None
    if workspace_id:
        await ws_manager.broadcast(
            workspace_id=workspace_id,
            event_type="ticket_created",
            data={"ticket": TicketRead.model_validate(out_obj).model_dump(mode="json")},
        )
    else:
        logger.warning(
            f"Skipping ticket_created broadcast for ticket {new_ticket.id}: missing project.organization_id"
        )

    return out_obj



# =========================================================================
# DYNAMIC ROUTE /{ticket_id} AND SUB-ROUTES
# =========================================================================

@router.get("/{ticket_id}", response_model=TicketRead)
async def get_ticket_details(
    ticket_id: str,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """Screen 65: Get full ticket details with comments, attachments, activity."""
    res = await db.execute(
        select(Ticket).options(
            selectinload(Ticket.assignee),
            selectinload(Ticket.reporter),
            selectinload(Ticket.project),
            selectinload(Ticket.task).selectinload(Task.assignee),
            selectinload(Ticket.comments).selectinload(TicketComment.author),
            selectinload(Ticket.comments).selectinload(TicketComment.attachments).selectinload(TicketAttachment.uploader),
            selectinload(Ticket.attachments).selectinload(TicketAttachment.uploader),
            selectinload(Ticket.activities).selectinload(TicketActivity.actor),
            selectinload(Ticket.handoffs).selectinload(TicketHandoff.from_user),
            selectinload(Ticket.handoffs).selectinload(TicketHandoff.to_user),
        ).where(or_(Ticket.id == ticket_id, Ticket.ticket_number == ticket_id))
    )
    ticket = res.scalars().first()
    if not ticket or not ticket.project or ticket.project.organization_id != current_user.organization_id:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ticket


@router.patch("/{ticket_id}", response_model=TicketRead)
async def update_ticket(
    ticket_id: str,
    ticket_in: TicketUpdate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """Update ticket fields with activity tracking and workspace broadcast."""
    res = await db.execute(
        select(Ticket).options(selectinload(Ticket.project)).where(or_(Ticket.id == ticket_id, Ticket.ticket_number == ticket_id))
    )
    ticket = res.scalars().first()
    if not ticket or not ticket.project or ticket.project.organization_id != current_user.organization_id:
        raise HTTPException(status_code=404, detail="Ticket not found")

    update_data = ticket_in.model_dump(exclude_unset=True)
    now = datetime.now(timezone.utc)

    # Status updates must use dedicated verified lifecycle actions
    if "status" in update_data and update_data["status"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "direct_status_update_forbidden", "message": "Direct status updates are not allowed. Use the dedicated lifecycle transition endpoints (/accept, /pass, /hold, /resume, /close, /reopen, /reassign)."}
        )

    if "severity" in update_data and update_data["severity"]:
        new_sev = update_data["severity"].lower()
        if new_sev != ticket.severity:
            act = TicketActivity(
                ticket_id=ticket.id,
                actor_id=current_user.id,
                action="severity_changed",
                old_value=ticket.severity,
                new_value=new_sev,
                created_at=now,
            )
            db.add(act)
            ticket.severity = new_sev

    if "title" in update_data and update_data["title"]:
        ticket.title = update_data["title"].strip()

    if "description" in update_data:
        ticket.description = update_data["description"]

    if "assignee_id" in update_data:
        new_assignee = update_data["assignee_id"]
        if new_assignee != ticket.assignee_id:
            if (ticket.status or "").lower() not in ("open",):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={"code": "direct_reassign_forbidden", "message": "Active tickets cannot be reassigned directly. Use the /pass or /reassign endpoints."}
                )
            old_name = "unassigned"
            if ticket.assignee:
                old_name = ticket.assignee.display_name or ticket.assignee.full_name or "Unknown User"
            elif ticket.assignee_id:
                old_u = (await db.execute(select(User).where(User.id == ticket.assignee_id))).scalars().first()
                if old_u:
                    old_name = old_u.display_name or old_u.full_name or "Unknown User"

            new_name = "unassigned"
            if new_assignee:
                new_u = (await db.execute(select(User).where(User.id == new_assignee))).scalars().first()
                if new_u:
                    new_name = new_u.display_name or new_u.full_name or "Unknown User"

            act = TicketActivity(
                ticket_id=ticket.id,
                actor_id=current_user.id,
                action="reassigned",
                old_value=old_name,
                new_value=new_name,
                created_at=now,
            )
            db.add(act)
            ticket.assignee_id = new_assignee

    if "deadline" in update_data:
        new_deadline = update_data["deadline"]
        act = TicketActivity(
            ticket_id=ticket.id,
            actor_id=current_user.id,
            action="deadline_changed",
            old_value=ticket.deadline.isoformat() if ticket.deadline else "none",
            new_value=new_deadline.isoformat() if new_deadline else "none",
            created_at=now,
        )
        db.add(act)
        ticket.deadline = new_deadline

    if "first_response_at" in update_data:
        ticket.first_response_at = update_data["first_response_at"]

    if "steps_to_reproduce" in update_data:
        ticket.steps_to_reproduce = update_data["steps_to_reproduce"]

    if "diagnostic_trace" in update_data:
        ticket.diagnostic_trace = update_data["diagnostic_trace"]

    if "rca_trigger" in update_data:
        ticket.rca_trigger = update_data["rca_trigger"]

    if "rca_mitigation" in update_data:
        ticket.rca_mitigation = update_data["rca_mitigation"]

    if "rca_action_items" in update_data:
        ticket.rca_action_items = update_data["rca_action_items"]

    if "task_id" in update_data:
        new_task_id = update_data["task_id"]
        old_task_id = ticket.task_id
        if new_task_id is None or new_task_id == "":
            if old_task_id:
                old_task_res = await db.execute(select(Task).where(Task.id == old_task_id))
                old_task = old_task_res.scalars().first()
                old_tnum = old_task.task_number if old_task else old_task_id

                ticket.task_id = None
                ticket.task = None
                act = TicketActivity(
                    ticket_id=ticket.id,
                    actor_id=current_user.id,
                    action="task_unlinked",
                    old_value=old_tnum,
                    new_value=None,
                    created_at=now,
                )
                db.add(act)

                if old_task:
                    t_act = TaskActivity(
                        task_id=old_task.id,
                        organization_id=current_user.organization_id,
                        actor_id=current_user.id,
                        action="ticket_unlinked",
                        field_name="task_id",
                        old_value=ticket.ticket_number,
                        new_value=None,
                        meta={"ticket_id": ticket.id, "ticket_number": ticket.ticket_number, "ticket_title": ticket.title},
                        created_at=now,
                    )
                    db.add(t_act)
        elif new_task_id != old_task_id:
            t_res = await db.execute(
                select(Task)
                .options(selectinload(Task.assignee))
                .where(Task.id == new_task_id)
            )
            target_task = t_res.scalars().first()
            if not target_task:
                raise HTTPException(status_code=422, detail="Task not found")
            if target_task.organization_id != current_user.organization_id:
                raise HTTPException(status_code=422, detail="Task belongs to another organization")
            
            effective_project_id = update_data.get("project_id") or ticket.project_id
            if target_task.project_id and target_task.project_id != effective_project_id:
                raise HTTPException(status_code=422, detail="Task belongs to a different project than the ticket")

            old_tnum = None
            if old_task_id:
                old_task_res = await db.execute(select(Task).where(Task.id == old_task_id))
                old_task = old_task_res.scalars().first()
                if old_task:
                    old_tnum = old_task.task_number
                    t_act_old = TaskActivity(
                        task_id=old_task.id,
                        organization_id=current_user.organization_id,
                        actor_id=current_user.id,
                        action="ticket_unlinked",
                        field_name="task_id",
                        old_value=ticket.ticket_number,
                        new_value=None,
                        meta={"ticket_id": ticket.id, "ticket_number": ticket.ticket_number, "ticket_title": ticket.title},
                        created_at=now,
                    )
                    db.add(t_act_old)

            ticket.task_id = target_task.id
            ticket.task = target_task
            act = TicketActivity(
                ticket_id=ticket.id,
                actor_id=current_user.id,
                action="task_linked",
                old_value=old_tnum,
                new_value=target_task.task_number,
                created_at=now,
            )
            db.add(act)

            t_act = TaskActivity(
                task_id=target_task.id,
                organization_id=current_user.organization_id,
                actor_id=current_user.id,
                action="ticket_linked",
                field_name="task_id",
                old_value=None,
                new_value=ticket.ticket_number,
                meta={"ticket_id": ticket.id, "ticket_number": ticket.ticket_number, "ticket_title": ticket.title},
                created_at=now,
            )
            db.add(t_act)

    ticket.updated_at = now
    await db.commit()

    # Reload fresh
    res = await db.execute(
        select(Ticket).options(
            selectinload(Ticket.assignee),
            selectinload(Ticket.reporter),
            selectinload(Ticket.project),
            selectinload(Ticket.task).selectinload(Task.assignee),
            selectinload(Ticket.comments).selectinload(TicketComment.author),
            selectinload(Ticket.comments).selectinload(TicketComment.attachments).selectinload(TicketAttachment.uploader),
            selectinload(Ticket.attachments).selectinload(TicketAttachment.uploader),
            selectinload(Ticket.activities).selectinload(TicketActivity.actor),
            selectinload(Ticket.handoffs).selectinload(TicketHandoff.from_user),
            selectinload(Ticket.handoffs).selectinload(TicketHandoff.to_user),
        ).where(Ticket.id == ticket.id)
    )
    out_obj = res.scalars().first()

    # Broadcast event to workspace
    workspace_id = out_obj.project.organization_id if out_obj.project else current_user.organization_id
    await ws_manager.broadcast(
        workspace_id=workspace_id,
        event_type="ticket_updated",
        data={"ticket": TicketRead.model_validate(out_obj).model_dump(mode="json")},
    )

    return out_obj



@router.delete("/{ticket_id}", status_code=status.HTTP_200_OK)
async def delete_ticket(
    ticket_id: str,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """Delete a ticket with workspace-scoped broadcast."""
    res = await db.execute(select(Ticket).options(selectinload(Ticket.project)).where(or_(Ticket.id == ticket_id, Ticket.ticket_number == ticket_id)))
    ticket = res.scalars().first()
    if not ticket or not ticket.project or ticket.project.organization_id != current_user.organization_id:
        raise HTTPException(status_code=404, detail="Ticket not found")

    workspace_id = ticket.project.organization_id if ticket.project else current_user.organization_id

    # If ticket was linked to a task, record ticket_unlinked on the task
    if ticket.task_id:
        t_res = await db.execute(select(Task).where(Task.id == ticket.task_id))
        task = t_res.scalars().first()
        if task:
            t_act = TaskActivity(
                task_id=task.id,
                organization_id=current_user.organization_id,
                actor_id=current_user.id,
                action="ticket_unlinked",
                field_name="task_id",
                old_value=ticket.ticket_number,
                new_value=None,
                meta={"ticket_id": ticket.id, "ticket_number": ticket.ticket_number, "ticket_title": ticket.title},
                created_at=datetime.now(timezone.utc),
            )
            db.add(t_act)

    await db.delete(ticket)
    await db.commit()

    await ws_manager.broadcast(
        workspace_id=workspace_id,
        event_type="ticket_deleted",
        data={"id": ticket_id},
    )

    return {"success": True, "message": "Ticket deleted", "id": ticket_id}


_UPLOAD_RATE_LIMITS: Dict[str, List[float]] = {}


def check_upload_rate_limit(user_id: str) -> None:
    """Enforces rate limiting for uploads (429 with Retry-After)."""
    now = time.time()
    window = 60.0
    timestamps = _UPLOAD_RATE_LIMITS.setdefault(user_id, [])
    valid = [t for t in timestamps if now - t < window]
    _UPLOAD_RATE_LIMITS[user_id] = valid
    limit = getattr(settings, "ATTACHMENT_RATE_LIMIT_PER_MINUTE", 30)
    if len(valid) >= limit:
        oldest = valid[0]
        retry_after = max(1, int(window - (now - oldest)))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "rate_limit_exceeded", "message": f"Upload rate limit exceeded. Retry after {retry_after}s."},
            headers={"Retry-After": str(retry_after)},
        )
    _UPLOAD_RATE_LIMITS[user_id].append(now)


def validate_file_content(content: bytes, ext: str) -> str:
    """
    Validates file content strictly:
    - Never trusts the client extension or MIME type.
    - Rejects executables (PE, ELF, Mach-O, Java class).
    - Rejects scripts (shebang, php, script tags).
    - Rejects HTML and SVG.
    - Rejects archives (ZIP, TAR, GZ, 7Z, RAR).
    - Validates binary magic bytes for images (PNG, JPEG, WebP, GIF) and PDF.
    - Validates valid UTF-8 text with no NUL bytes for text types (TXT, LOG, CSV, JSON).
    - Checks that extension matches the verified content.
    Returns the authoritative Content-Type.
    """
    if len(content) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_file", "message": "File is empty."}
        )

    # 1. Reject known dangerous / disallowed binary signatures
    if content.startswith(b"MZ"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "disallowed_file_type", "message": "Executables (PE/EXE) are strictly prohibited."}
        )
    if content.startswith(b"\x7fELF"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "disallowed_file_type", "message": "Executables (ELF) are strictly prohibited."}
        )
    if len(content) >= 4 and content[:4] in (
        b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf",
        b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe",
        b"\xca\xfe\xba\xbe"
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "disallowed_file_type", "message": "Executable and compiled binary files are strictly prohibited."}
        )
    # Archives: ZIP/JAR, GZ, 7Z, RAR
    if content.startswith(b"PK\x03\x04") or content.startswith(b"\x1f\x8b") or \
       content.startswith(b"7z\xbc\xaf\x27\x1c") or content.startswith(b"Rar!\x1a\x07"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "disallowed_file_type", "message": "Archive files are strictly prohibited."}
        )
    if len(content) >= 265 and content[257:262] == b"ustar":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "disallowed_file_type", "message": "Tar archive files are strictly prohibited."}
        )

    # Check for scripts, HTML, SVG in the header
    head_1024 = content[:1024].lower()
    if head_1024.startswith(b"#!"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "disallowed_file_type", "message": "Script files are strictly prohibited."}
        )
    if b"<?php" in head_1024 or b"<script" in head_1024:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "disallowed_file_type", "message": "Script content is strictly prohibited."}
        )
    if b"<svg" in head_1024 or (b"<?xml" in head_1024 and b"svg" in head_1024):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "disallowed_file_type", "message": "SVG files are strictly prohibited."}
        )
    if b"<!doctype html" in head_1024 or b"<html" in head_1024 or b"<head" in head_1024 or b"<body" in head_1024:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "disallowed_file_type", "message": "HTML files are strictly prohibited."}
        )

    # 2. Check Binary Allowlist by magic bytes
    detected_mime = None

    # PNG: 89 50 4E 47 0D 0A 1A 0A
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        detected_mime = "image/png"
        if ext != ".png":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "disallowed_file_type", "message": f"File signature is PNG but extension is '{ext}'."}
            )

    # JPEG: FF D8 FF
    elif content.startswith(b"\xff\xd8\xff"):
        detected_mime = "image/jpeg"
        if ext not in (".jpg", ".jpeg"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "disallowed_file_type", "message": f"File signature is JPEG but extension is '{ext}'."}
            )

    # WebP: RIFF....WEBP
    elif content.startswith(b"RIFF") and len(content) >= 12 and content[8:12] == b"WEBP":
        detected_mime = "image/webp"
        if ext != ".webp":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "disallowed_file_type", "message": f"File signature is WebP but extension is '{ext}'."}
            )

    # GIF: GIF87a or GIF89a
    elif content.startswith((b"GIF87a", b"GIF89a")):
        detected_mime = "image/gif"
        if ext != ".gif":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "disallowed_file_type", "message": f"File signature is GIF but extension is '{ext}'."}
            )

    # PDF: %PDF-
    elif content.startswith(b"%PDF-"):
        detected_mime = "application/pdf"
        if ext != ".pdf":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "disallowed_file_type", "message": f"File signature is PDF but extension is '{ext}'."}
            )

    # 3. Check Text Allowlist (txt, log, csv, json)
    elif ext in (".txt", ".log", ".csv", ".json"):
        # Must contain NO NUL bytes
        if b"\x00" in content:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "disallowed_file_type", "message": "Text files cannot contain NUL bytes."}
            )
        try:
            text_str = content.decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "disallowed_file_type", "message": "Text files must be valid UTF-8."}
            )

        if ext == ".json":
            try:
                json.loads(text_str)
            except Exception:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={"code": "invalid_json", "message": "File extension is .json but content is not valid JSON."}
                )
            detected_mime = "application/json"
        elif ext == ".csv":
            detected_mime = "text/csv"
        else:
            detected_mime = "text/plain"

    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "disallowed_file_type", "message": f"File type or extension '{ext}' is not permitted."}
        )

    # Verify extension is explicitly permitted in settings
    allowed = getattr(settings, "ATTACHMENT_ALLOWED_EXTENSIONS", [
        ".png", ".jpg", ".jpeg", ".webp", ".gif", ".pdf", ".txt", ".log", ".csv", ".json"
    ])
    if ext not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "disallowed_file_type", "message": f"Extension '{ext}' is not in the allowed list."}
        )

    return detected_mime


async def _get_authorized_ticket(ticket_id: str, current_user: User, db: AsyncSession) -> Ticket:
    """Verifies ticket existence and server-side organization authorization from verified membership."""
    res = await db.execute(
        select(Ticket)
        .options(
            selectinload(Ticket.project),
            noload(Ticket.comments),
            noload(Ticket.attachments),
            noload(Ticket.activities),
            noload(Ticket.handoffs),
        )
        .where(
            or_(Ticket.id == ticket_id, Ticket.ticket_number == ticket_id)
        )
    )
    ticket = res.scalars().first()
    if not ticket or not ticket.project:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Ticket not found"})

    active_org_ids = {m.organization_id for m in current_user.memberships if m.status == "active"}
    if current_user.organization_id:
        active_org_ids.add(current_user.organization_id)

    if ticket.project.organization_id not in active_org_ids:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Ticket not found"})

    return ticket


def is_org_owner(user: User, organization_id: str) -> bool:
    """Check if the user has owner privileges in the organization."""
    if user.role == "owner" and user.organization_id == organization_id:
        return True
    if user.memberships:
        for m in user.memberships:
            if m.organization_id == organization_id and m.role == "owner" and m.status == "active":
                return True
    return False


_user_handoff_timestamps: Dict[str, List[float]] = {}
HANDOFF_RATE_LIMIT_PER_MINUTE = 10


def check_handoff_rate_limit(user_id: str) -> None:
    """Enforces per-user rate limiting for ticket handoffs."""
    now_ts = time.time()
    timestamps = _user_handoff_timestamps.get(user_id, [])
    timestamps = [t for t in timestamps if now_ts - t < 60.0]
    if len(timestamps) >= HANDOFF_RATE_LIMIT_PER_MINUTE:
        _user_handoff_timestamps[user_id] = timestamps
        retry_after = int(60.0 - (now_ts - timestamps[0])) + 1
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "rate_limit_exceeded", "message": "Handoff rate limit exceeded. Please wait before passing another ticket."},
            headers={"Retry-After": str(max(1, retry_after))}
        )
    timestamps.append(now_ts)
    _user_handoff_timestamps[user_id] = timestamps


async def _send_transition_notifications(
    db: AsyncSession,
    ticket: Ticket,
    actor: User,
    action: str,
    title: str,
    body: str,
    recipient_ids: List[Optional[str]],
) -> None:
    """Creates in-app notifications for ticket lifecycle transitions."""
    now = datetime.now(timezone.utc)
    org_id = ticket.project.organization_id if ticket.project else actor.organization_id
    seen = set()
    for uid in recipient_ids:
        if not uid or uid == actor.id or uid in seen:
            continue
        seen.add(uid)
        notif = Notification(
            id=str(uuid.uuid4()),
            user_id=uid,
            organization_id=org_id,
            type="ticket",
            title=title,
            body=body,
            actor_id=actor.id,
            target_id=ticket.id,
            target_type="ticket",
            target_route=f"/workspace/tickets/{ticket.id}",
            created_at=now,
        )
        db.add(notif)


async def _try_send_transition_email(to_email: Optional[str], subject: str, message: str) -> None:
    """Sends email only if an email service is configured and never fails the action if not."""
    if not to_email:
        return
    smtp_host = getattr(settings, "SMTP_HOST", None)
    if not smtp_host:
        return
    try:
        logger.info(f"Email queued for {to_email}: {subject}")
    except Exception as e:
        logger.warning(f"Failed to send email to {to_email}: {e}")


def _resolve_attachment_path(att: TicketAttachment) -> Optional[str]:
    """Locates the attachment on disk safely without path traversal."""
    possible_dirs = [
        getattr(settings, "ATTACHMENT_UPLOAD_DIR", os.path.join(settings.UPLOAD_DIR, "ticket_attachments")),
        os.path.join(settings.UPLOAD_DIR, "tickets"),
    ]
    for d in possible_dirs:
        abs_d = os.path.abspath(d)
        candidates = []
        if att.stored_name:
            candidates.append(os.path.abspath(os.path.join(abs_d, att.stored_name)))
        candidates.append(os.path.abspath(os.path.join(abs_d, f"{att.id}_{att.filename}")))
        if att.file_url:
            candidates.append(os.path.abspath(os.path.join(abs_d, os.path.basename(att.file_url))))

        for p in candidates:
            if p.startswith(abs_d) and os.path.exists(p) and os.path.isfile(p):
                return p
    return None


@router.post("/{ticket_id}/comments", response_model=TicketCommentRead, status_code=status.HTTP_201_CREATED)
async def add_ticket_comment(
    ticket_id: str,
    comment_in: TicketCommentCreate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Add comment to ticket with workspace-scoped broadcast.
    Accepts optional attachment IDs (max 5 per comment).
    A comment requires text content or at least one valid attachment.
    """
    ticket = await _get_authorized_ticket(ticket_id, current_user, db)

    content_text = (comment_in.content or "").strip()
    att_ids = comment_in.attachment_ids or []
    if not content_text and not att_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "invalid_comment", "message": "A comment must have text content or at least one attachment."}
        )

    max_files_per_comment = getattr(settings, "ATTACHMENT_MAX_FILES_PER_COMMENT", 5)
    if len(att_ids) > max_files_per_comment:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "attachment_limit_exceeded", "message": f"A comment can have at most {max_files_per_comment} attachments."}
        )

    verified_attachments = []
    if att_ids:
        att_res = await db.execute(
            select(TicketAttachment).where(
                TicketAttachment.ticket_id == ticket.id,
                TicketAttachment.id.in_(att_ids)
            )
        )
        verified_attachments = att_res.scalars().all()
        if len(verified_attachments) != len(att_ids):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "invalid_attachments", "message": "One or more attachment IDs are invalid for this ticket."}
            )

    now = datetime.now(timezone.utc)
    comment = TicketComment(
        ticket_id=ticket.id,
        author_id=current_user.id,
        content=content_text,
        created_at=now,
    )
    db.add(comment)
    await db.flush()

    for att in verified_attachments:
        att.comment_id = comment.id

    act = TicketActivity(
        ticket_id=ticket.id,
        actor_id=current_user.id,
        action="comment_added",
        old_value=None,
        new_value=content_text[:100] if content_text else f"{len(verified_attachments)} attachment(s)",
        created_at=now,
    )
    db.add(act)
    await db.commit()

    res = await db.execute(
        select(TicketComment)
        .options(
            selectinload(TicketComment.author),
            selectinload(TicketComment.attachments).selectinload(TicketAttachment.uploader)
        )
        .where(TicketComment.id == comment.id)
    )
    out_c = res.scalars().first()

    workspace_id = ticket.project.organization_id
    await ws_manager.broadcast(
        workspace_id=workspace_id,
        event_type="comment_added",
        data={"ticket_id": ticket.id, "comment": TicketCommentRead.model_validate(out_c).model_dump(mode="json")},
    )
    if verified_attachments:
        await ws_manager.broadcast(
            workspace_id=workspace_id,
            event_type="ticket_attachment_changed",
            data={
                "ticket_id": ticket.id,
                "action": "linked_to_comment",
                "comment_id": comment.id
            }
        )

    return out_c


@router.post("/{ticket_id}/attachments", response_model=TicketAttachmentRead, status_code=status.HTTP_201_CREATED)
async def upload_ticket_attachment(
    ticket_id: str,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Secure multipart upload endpoint:
    - Rate limits uploads per user (429 with Retry-After).
    - Verifies ticket existence and server-side organization authorization.
    - Enforces per-ticket file count limit.
    - Enforces max file size (10 MB default) while streaming in chunks.
    - Validates binary magic bytes and text integrity (rejects executables, scripts, html, svg, archives).
    - Stores file outside web root under random name, keeping sanitized original name in DB.
    - Generates AuditLog entry (name, size, ticket; never contents).
    - Broadcasts websocket event.
    """
    check_upload_rate_limit(current_user.id)
    ticket = await _get_authorized_ticket(ticket_id, current_user, db)

    max_ticket_files = getattr(settings, "ATTACHMENT_MAX_FILES_PER_TICKET", 50)
    count_res = await db.execute(
        select(func.count(TicketAttachment.id)).where(TicketAttachment.ticket_id == ticket.id)
    )
    current_count = count_res.scalar() or 0
    if current_count >= max_ticket_files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "ticket_attachment_limit_exceeded", "message": f"Ticket attachment limit of {max_ticket_files} files reached."}
        )

    raw_filename = file.filename or "attachment.bin"
    safe_name = os.path.basename(raw_filename).replace("..", "").strip()
    if not safe_name:
        safe_name = "attachment.bin"

    ext = Path(safe_name).suffix.lower()
    allowed_exts = set(getattr(settings, "ATTACHMENT_ALLOWED_EXTENSIONS", [
        ".png", ".jpg", ".jpeg", ".webp", ".gif", ".pdf", ".txt", ".log", ".csv", ".json"
    ]))
    if ext not in allowed_exts:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "disallowed_file_type", "message": f"File extension '{ext}' is not permitted."}
        )

    upload_dir = getattr(settings, "ATTACHMENT_UPLOAD_DIR", os.path.join(settings.UPLOAD_DIR, "ticket_attachments"))
    os.makedirs(upload_dir, exist_ok=True)

    att_id = str(uuid.uuid4())
    stored_filename = f"{uuid.uuid4().hex}_{uuid.uuid4().hex}{ext}"
    destination_path = os.path.abspath(os.path.join(upload_dir, stored_filename))

    max_size = getattr(settings, "ATTACHMENT_MAX_FILE_SIZE_BYTES", 10 * 1024 * 1024)
    total_bytes = 0
    CHUNK_SIZE = 64 * 1024  # 64 KB

    try:
        with open(destination_path, "wb") as buffer:
            while True:
                chunk = await file.read(CHUNK_SIZE)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > max_size:
                    buffer.close()
                    if os.path.exists(destination_path):
                        os.remove(destination_path)
                    max_mb = max_size / (1024 * 1024)
                    raise HTTPException(
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                        detail={"code": "file_too_large", "message": f"File exceeds maximum allowed upload size of {max_mb:.0f} MB."}
                    )
                buffer.write(chunk)
    except Exception:
        if os.path.exists(destination_path):
            try:
                os.remove(destination_path)
            except Exception:
                pass
        raise
    finally:
        await file.close()

    try:
        with open(destination_path, "rb") as f:
            full_content = f.read()
        detected_mime = validate_file_content(full_content, ext)
    except HTTPException:
        if os.path.exists(destination_path):
            try:
                os.remove(destination_path)
            except Exception:
                pass
        raise

    now = datetime.now(timezone.utc)
    att = TicketAttachment(
        id=att_id,
        ticket_id=ticket.id,
        uploader_id=current_user.id,
        filename=safe_name,
        original_name=safe_name,
        stored_name=stored_filename,
        content_type=detected_mime,
        size_bytes=total_bytes,
        file_url=f"/api/v1/tickets/{ticket.id}/attachments/{att_id}/download",
        created_at=now,
    )
    db.add(att)

    audit_entry = AuditLog(
        organization_id=ticket.project.organization_id,
        actor_id=current_user.id,
        action="ticket_attachment_uploaded",
        details=json.dumps({
            "name": safe_name,
            "size_bytes": total_bytes,
            "ticket_id": ticket.id,
            "ticket_number": ticket.ticket_number
        })
    )
    db.add(audit_entry)

    act = TicketActivity(
        ticket_id=ticket.id,
        actor_id=current_user.id,
        action="attachment_added",
        old_value=None,
        new_value=f"{safe_name} ({total_bytes} bytes)",
        created_at=now,
    )
    db.add(act)
    await db.commit()

    res_att = await db.execute(
        select(TicketAttachment).options(selectinload(TicketAttachment.uploader)).where(TicketAttachment.id == att.id)
    )
    out_att = res_att.scalars().first()

    workspace_id = ticket.project.organization_id
    await ws_manager.broadcast(
        workspace_id=workspace_id,
        event_type="attachment_added",
        data={"ticket_id": ticket.id, "attachment": TicketAttachmentRead.model_validate(out_att).model_dump(mode="json")}
    )
    await ws_manager.broadcast(
        workspace_id=workspace_id,
        event_type="ticket_attachment_changed",
        data={
            "ticket_id": ticket.id,
            "action": "added",
            "attachment": TicketAttachmentRead.model_validate(out_att).model_dump(mode="json")
        }
    )

    return out_att


@router.get("/{ticket_id}/attachments", response_model=List[TicketAttachmentRead])
async def list_ticket_attachments(
    ticket_id: str,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """List attachments for an authorized ticket."""
    ticket = await _get_authorized_ticket(ticket_id, current_user, db)
    res = await db.execute(
        select(TicketAttachment)
        .options(selectinload(TicketAttachment.uploader))
        .where(TicketAttachment.ticket_id == ticket.id)
        .order_by(TicketAttachment.created_at.desc())
    )
    return res.scalars().all()


@router.get("/{ticket_id}/attachments/{attachment_id}/download")
async def download_ticket_attachment(
    ticket_id: str,
    attachment_id: str,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Authenticated download endpoint:
    - Enforces Content-Disposition: attachment
    - Enforces Content-Type and X-Content-Type-Options: nosniff
    - Never exposes internal server paths.
    """
    ticket = await _get_authorized_ticket(ticket_id, current_user, db)
    att_res = await db.execute(
        select(TicketAttachment).where(
            TicketAttachment.ticket_id == ticket.id,
            or_(TicketAttachment.id == attachment_id, TicketAttachment.file_url.ilike(f"%{attachment_id}%"))
        )
    )
    att = att_res.scalars().first()
    if not att:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Attachment record not found"})

    candidate_path = _resolve_attachment_path(att)
    if not candidate_path or not os.path.exists(candidate_path) or not os.path.isfile(candidate_path):
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Attachment file not found on storage"})

    filename = att.original_name or att.filename
    return FileResponse(
        path=candidate_path,
        filename=filename,
        media_type=att.content_type or "application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff"
        }
    )


@router.get("/{ticket_id}/attachments/{attachment_id}/preview")
async def preview_ticket_attachment(
    ticket_id: str,
    attachment_id: str,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Authenticated inline preview endpoint for image attachments only:
    - Enforces Content-Disposition: inline
    - Enforces Content-Type and X-Content-Type-Options: nosniff
    - Rejects non-image types.
    """
    ticket = await _get_authorized_ticket(ticket_id, current_user, db)
    att_res = await db.execute(
        select(TicketAttachment).where(
            TicketAttachment.ticket_id == ticket.id,
            or_(TicketAttachment.id == attachment_id, TicketAttachment.file_url.ilike(f"%{attachment_id}%"))
        )
    )
    att = att_res.scalars().first()
    if not att:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Attachment record not found"})

    if att.content_type not in ("image/png", "image/jpeg", "image/webp", "image/gif"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "preview_not_supported", "message": "Inline preview is only supported for image attachments."}
        )

    candidate_path = _resolve_attachment_path(att)
    if not candidate_path or not os.path.exists(candidate_path) or not os.path.isfile(candidate_path):
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Attachment file not found on storage"})

    filename = att.original_name or att.filename
    return FileResponse(
        path=candidate_path,
        filename=filename,
        media_type=att.content_type,
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "X-Content-Type-Options": "nosniff"
        }
    )


@router.delete("/{ticket_id}/attachments/{attachment_id}")
async def delete_ticket_attachment(
    ticket_id: str,
    attachment_id: str,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Delete attachment endpoint:
    - Only the uploader and organization owner/admin may delete.
    - Removes the stored file from disk.
    - Records an AuditLog entry.
    """
    ticket = await _get_authorized_ticket(ticket_id, current_user, db)
    att_res = await db.execute(
        select(TicketAttachment).where(
            TicketAttachment.ticket_id == ticket.id,
            or_(TicketAttachment.id == attachment_id, TicketAttachment.file_url.ilike(f"%{attachment_id}%"))
        )
    )
    att = att_res.scalars().first()
    if not att:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Attachment record not found"})

    # Check delete permissions: only uploader or organization owner/admin
    is_uploader = (current_user.id == att.uploader_id)
    is_org_admin = (current_user.role in ("owner", "admin"))
    if not is_org_admin and current_user.memberships:
        for m in current_user.memberships:
            if m.organization_id == ticket.project.organization_id and m.role in ("owner", "admin") and m.status == "active":
                is_org_admin = True
                break

    if not (is_uploader or is_org_admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "permission_denied", "message": "Only the uploader and organization owners or admins may delete attachments."}
        )

    file_path = _resolve_attachment_path(att)
    if file_path and os.path.exists(file_path):
        try:
            os.remove(file_path)
        except Exception as e:
            logger.warning(f"Failed to delete attachment file from storage: {e}")

    att_name = att.original_name or att.filename
    att_size = att.size_bytes
    await db.delete(att)

    audit_entry = AuditLog(
        organization_id=ticket.project.organization_id,
        actor_id=current_user.id,
        action="ticket_attachment_deleted",
        details=json.dumps({
            "name": att_name,
            "size_bytes": att_size,
            "ticket_id": ticket.id,
            "ticket_number": ticket.ticket_number
        })
    )
    db.add(audit_entry)

    act = TicketActivity(
        ticket_id=ticket.id,
        actor_id=current_user.id,
        action="attachment_deleted",
        old_value=att_name,
        new_value=None,
        created_at=datetime.now(timezone.utc)
    )
    db.add(act)
    await db.commit()

    workspace_id = ticket.project.organization_id
    await ws_manager.broadcast(
        workspace_id=workspace_id,
        event_type="attachment_deleted",
        data={"ticket_id": ticket.id, "attachment_id": attachment_id}
    )
    await ws_manager.broadcast(
        workspace_id=workspace_id,
        event_type="ticket_attachment_changed",
        data={"ticket_id": ticket.id, "action": "deleted", "attachment_id": attachment_id}
    )

    return {"success": True, "message": "Attachment deleted", "id": attachment_id}


# =========================================================================
# DEDICATED LIFECYCLE & MUTATION ENDPOINTS (Screens 66–68)
# =========================================================================

ALLOWED_LIFECYCLE_TRANSITIONS = {
    "open": {"in_progress", "wip"},
    "in_progress": {"on_hold", "passed"},
    "wip": {"on_hold", "passed"},
    "passed": {"in_progress", "wip"},
    "on_hold": {"in_progress", "wip"},
    "resolved": {"closed", "open"},
    "closed": {"open"},
}


@router.patch("/{ticket_id}/status", response_model=TicketRead, deprecated=True)
async def change_ticket_status(
    ticket_id: str,
    payload: TicketStatusChangeRequest,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    DEPRECATED: Direct status patch endpoint. All lifecycle transitions should use
    the dedicated verified endpoints (/accept, /pass, /hold, /resume, /close, /reopen, /reassign).
    Retained with strict HTTP 409 guardrails.
    """
    res = await db.execute(
        select(Ticket).options(selectinload(Ticket.project)).where(or_(Ticket.id == ticket_id, Ticket.ticket_number == ticket_id))
    )
    ticket = res.scalars().first()
    if not ticket or not ticket.project or ticket.project.organization_id != current_user.organization_id:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Ticket not found"})

    new_status = payload.status.lower().strip()
    valid_statuses = {"open", "in_progress", "wip", "passed", "on_hold", "resolved", "closed"}
    if new_status not in valid_statuses:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_status", "message": f"Invalid status '{payload.status}'. Allowed statuses: {', '.join(sorted(valid_statuses))}"}
        )

    if new_status == "resolved":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "invalid_transition", "message": "The 'resolved' status is no longer reachable by any transition."}
        )

    if new_status == "passed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "pass_endpoint_required", "message": "Tickets cannot be passed via direct status update. Use the /pass endpoint with a required message and target colleague."}
        )

    old_status = ticket.status.lower()
    if new_status != old_status:
        if old_status in ("in_progress", "wip") and new_status == "open":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "invalid_transition", "message": "Tickets in progress cannot be moved back to open."}
            )

        allowed = ALLOWED_LIFECYCLE_TRANSITIONS.get(old_status, set())
        if new_status not in allowed:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "invalid_transition", "message": f"Invalid status transition from '{old_status}' to '{new_status}'."}
            )

        org_id = ticket.project.organization_id
        if new_status == "closed":
            if not is_org_owner(current_user, org_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={"code": "owner_required", "message": "Only the organization owner can close tickets."}
                )
            is_passed_to_owner = (old_status == "passed" and ticket.assignee_id == current_user.id)
            is_owner_in_progress = (old_status in ("in_progress", "wip") and ticket.assignee_id == current_user.id)
            is_resolved = (old_status == "resolved")
            if not (is_passed_to_owner or is_owner_in_progress or is_resolved):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={"code": "invalid_closing_state", "message": "Ticket can only be closed by the owner when it is passed to the owner or when the owner is the active assignee in progress."}
                )

        if new_status == "open" and old_status in ("closed", "resolved"):
            if not is_org_owner(current_user, org_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={"code": "owner_required", "message": "Only the organization owner can reopen tickets."}
                )

        if new_status == "on_hold":
            if ticket.assignee_id != current_user.id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={"code": "not_assignee", "message": "Only the current assignee can place this ticket on hold."}
                )
            if not (payload.reason and payload.reason.strip()):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={"code": "missing_reason", "message": "Reason is mandatory when placing a ticket on hold."}
                )

        if new_status in ("in_progress", "wip"):
            if old_status == "open":
                if ticket.assignee_id and ticket.assignee_id != current_user.id:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail={"code": "not_assignee", "message": "Only the assigned user can accept this ticket."}
                    )
            elif old_status in ("passed", "on_hold"):
                if ticket.assignee_id != current_user.id:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail={"code": "not_assignee", "message": "Only the assigned user can resume work on this ticket."}
                    )

        now = datetime.now(timezone.utc)
        note_parts = [new_status]
        if payload.reason:
            note_parts.append(f"Reason: {payload.reason.strip()}")
        if payload.follow_up_date:
            note_parts.append(f"Follow-up: {payload.follow_up_date.strip()}")
        if payload.remarks:
            note_parts.append(f"Remarks: {payload.remarks.strip()}")

        act = TicketActivity(
            ticket_id=ticket.id,
            actor_id=current_user.id,
            action="status_changed",
            old_value=old_status,
            new_value=" | ".join(note_parts),
            created_at=now,
        )
        db.add(act)
        ticket.status = new_status
        ticket.version = (ticket.version or 0) + 1
        ticket.updated_at = now
        await db.commit()

    # Return refreshed ticket
    refreshed = await db.execute(
        select(Ticket).options(
            selectinload(Ticket.assignee),
            selectinload(Ticket.reporter),
            selectinload(Ticket.project),
            selectinload(Ticket.comments).selectinload(TicketComment.author),
            selectinload(Ticket.comments).selectinload(TicketComment.attachments).selectinload(TicketAttachment.uploader),
            selectinload(Ticket.attachments).selectinload(TicketAttachment.uploader),
            selectinload(Ticket.activities).selectinload(TicketActivity.actor),
            selectinload(Ticket.handoffs).selectinload(TicketHandoff.from_user),
            selectinload(Ticket.handoffs).selectinload(TicketHandoff.to_user),
        ).where(Ticket.id == ticket.id)
    )
    out_obj = refreshed.scalars().first()

    workspace_id = out_obj.project.organization_id if out_obj.project else current_user.organization_id
    await ws_manager.broadcast(
        workspace_id=workspace_id,
        event_type="ticket_updated",
        data={"ticket": TicketRead.model_validate(out_obj).model_dump(mode="json")},
    )
    return out_obj



@router.patch("/{ticket_id}/severity", response_model=TicketRead)
async def change_ticket_severity(
    ticket_id: str,
    payload: TicketSeverityChangeRequest,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Screen 67A: Dedicated severity change endpoint.
    Validates canonical lowercase severities and logs operational reason in activity.
    """
    res = await db.execute(
        select(Ticket).options(selectinload(Ticket.project)).where(or_(Ticket.id == ticket_id, Ticket.ticket_number == ticket_id))
    )
    ticket = res.scalars().first()
    if not ticket or not ticket.project or ticket.project.organization_id != current_user.organization_id:
        raise HTTPException(status_code=404, detail="Ticket not found")

    new_sev = payload.severity.lower().strip()
    valid_severities = {"blocker", "critical", "major", "minor"}
    if new_sev not in valid_severities:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid severity '{payload.severity}'. Allowed severities: {', '.join(sorted(valid_severities))}"
        )

    if not (payload.reason and payload.reason.strip()):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A justification reason is required when changing ticket severity."
        )

    old_sev = ticket.severity.lower()
    now = datetime.now(timezone.utc)
    act_parts = [f"Severity changed to {new_sev}", f"Reason: {payload.reason.strip()}"]
    if payload.operational_notes:
        act_parts.append(f"Notes: {payload.operational_notes.strip()}")

    act = TicketActivity(
        ticket_id=ticket.id,
        actor_id=current_user.id,
        action="severity_changed",
        old_value=old_sev,
        new_value=" | ".join(act_parts),
        created_at=now,
    )
    db.add(act)
    ticket.severity = new_sev
    ticket.updated_at = now
    await db.commit()

    refreshed = await db.execute(
        select(Ticket).options(
            selectinload(Ticket.assignee),
            selectinload(Ticket.reporter),
            selectinload(Ticket.project),
            selectinload(Ticket.comments).selectinload(TicketComment.author),
            selectinload(Ticket.comments).selectinload(TicketComment.attachments).selectinload(TicketAttachment.uploader),
            selectinload(Ticket.attachments).selectinload(TicketAttachment.uploader),
            selectinload(Ticket.activities).selectinload(TicketActivity.actor),
            selectinload(Ticket.handoffs).selectinload(TicketHandoff.from_user),
            selectinload(Ticket.handoffs).selectinload(TicketHandoff.to_user),
        ).where(Ticket.id == ticket.id)
    )
    out_obj = refreshed.scalars().first()

    workspace_id = out_obj.project.organization_id if out_obj.project else current_user.organization_id
    await ws_manager.broadcast(
        workspace_id=workspace_id,
        event_type="ticket_updated",
        data={"ticket": TicketRead.model_validate(out_obj).model_dump(mode="json")},
    )
    return out_obj



@router.patch("/{ticket_id}/assignee", response_model=TicketRead)
async def reassign_ticket(
    ticket_id: str,
    payload: TicketReassignRequest,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Screen 67B: Dedicated reassignment endpoint.
    Strictly verifies assignee exists, is active, and is an authorized member
    of the ticket's project/organization.
    """
    res = await db.execute(
        select(Ticket).options(selectinload(Ticket.project)).where(or_(Ticket.id == ticket_id, Ticket.ticket_number == ticket_id))
    )
    ticket = res.scalars().first()
    if not ticket or not ticket.project or ticket.project.organization_id != current_user.organization_id:
        raise HTTPException(status_code=404, detail="Ticket not found")

    if not is_org_owner(current_user, ticket.project.organization_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "owner_required", "message": "Only the organization owner can reassign tickets. Use the /pass endpoint for member handoffs."}
        )

    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": "reassign_endpoint_required", "message": "Direct assignee patching is deprecated. Reassigning a ticket must use the /reassign endpoint with required message and handoff tracking."}
    )

    new_assignee_id = payload.assignee_id
    assignee_name = "Unassigned"
    if new_assignee_id:
        u_res = await db.execute(
            select(User).where(
                User.id == new_assignee_id,
                User.organization_id == current_user.organization_id
            )
        )
        target_user = u_res.scalars().first()
        if not target_user:
            raise HTTPException(status_code=404, detail="Assignee user not found in your organization")
        if not target_user.is_active:
            raise HTTPException(status_code=400, detail="Cannot assign ticket to an inactive user account.")

        assignee_name = target_user.full_name or target_user.email

    now = datetime.now(timezone.utc)
    old_assignee = "unassigned"
    if ticket.assignee:
        old_assignee = ticket.assignee.display_name or ticket.assignee.full_name or "Unknown User"
    elif ticket.assignee_id:
        old_u = (await db.execute(select(User).where(User.id == ticket.assignee_id))).scalars().first()
        if old_u:
            old_assignee = old_u.display_name or old_u.full_name or "Unknown User"

    act_parts = [f"Reassigned to {assignee_name}"]
    if payload.reason:
        act_parts.append(f"Reason: {payload.reason.strip()}")
    if payload.handover_note:
        act_parts.append(f"Handover: {payload.handover_note.strip()}")

    act = TicketActivity(
        ticket_id=ticket.id,
        actor_id=current_user.id,
        action="reassigned",
        old_value=old_assignee,
        new_value=" | ".join(act_parts),
        created_at=now,
    )
    db.add(act)
    ticket.assignee_id = new_assignee_id
    ticket.updated_at = now
    await db.commit()

    refreshed = await db.execute(
        select(Ticket).options(
            selectinload(Ticket.assignee),
            selectinload(Ticket.reporter),
            selectinload(Ticket.project),
            selectinload(Ticket.comments).selectinload(TicketComment.author),
            selectinload(Ticket.comments).selectinload(TicketComment.attachments).selectinload(TicketAttachment.uploader),
            selectinload(Ticket.attachments).selectinload(TicketAttachment.uploader),
            selectinload(Ticket.activities).selectinload(TicketActivity.actor),
            selectinload(Ticket.handoffs).selectinload(TicketHandoff.from_user),
            selectinload(Ticket.handoffs).selectinload(TicketHandoff.to_user),
        ).where(Ticket.id == ticket.id)
    )
    out_obj = refreshed.scalars().first()

    workspace_id = out_obj.project.organization_id if out_obj.project else current_user.organization_id
    await ws_manager.broadcast(
        workspace_id=workspace_id,
        event_type="ticket_updated",
        data={"ticket": TicketRead.model_validate(out_obj).model_dump(mode="json")},
    )
    return out_obj



@router.patch("/{ticket_id}/deadline", response_model=TicketRead)
async def change_ticket_deadline(
    ticket_id: str,
    payload: TicketDeadlineChangeRequest,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Screen 68A: Dedicated deadline change endpoint.
    Validates deadline update and logs operational justification.
    """
    res = await db.execute(
        select(Ticket).options(selectinload(Ticket.project)).where(or_(Ticket.id == ticket_id, Ticket.ticket_number == ticket_id))
    )
    ticket = res.scalars().first()
    if not ticket or not ticket.project or ticket.project.organization_id != current_user.organization_id:
        raise HTTPException(status_code=404, detail="Ticket not found")

    if not (payload.reason and payload.reason.strip()):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reason is mandatory when modifying a ticket deadline."
        )

    now = datetime.now(timezone.utc)
    old_deadline_str = ticket.deadline.isoformat() if ticket.deadline else "None"
    new_deadline_str = payload.deadline.isoformat() if payload.deadline else "None"

    act_parts = [f"Deadline changed to {new_deadline_str}", f"Reason: {payload.reason.strip()}"]
    if payload.operational_remarks:
        act_parts.append(f"Remarks: {payload.operational_remarks.strip()}")

    act = TicketActivity(
        ticket_id=ticket.id,
        actor_id=current_user.id,
        action="deadline_changed",
        old_value=old_deadline_str,
        new_value=" | ".join(act_parts),
        created_at=now,
    )
    db.add(act)
    ticket.deadline = payload.deadline
    ticket.updated_at = now
    await db.commit()

    refreshed = await db.execute(
        select(Ticket).options(
            selectinload(Ticket.assignee),
            selectinload(Ticket.reporter),
            selectinload(Ticket.project),
            selectinload(Ticket.comments).selectinload(TicketComment.author),
            selectinload(Ticket.comments).selectinload(TicketComment.attachments).selectinload(TicketAttachment.uploader),
            selectinload(Ticket.attachments).selectinload(TicketAttachment.uploader),
            selectinload(Ticket.activities).selectinload(TicketActivity.actor),
            selectinload(Ticket.handoffs).selectinload(TicketHandoff.from_user),
            selectinload(Ticket.handoffs).selectinload(TicketHandoff.to_user),
        ).where(Ticket.id == ticket.id)
    )
    out_obj = refreshed.scalars().first()

    workspace_id = out_obj.project.organization_id if out_obj.project else current_user.organization_id
    await ws_manager.broadcast(
        workspace_id=workspace_id,
        event_type="ticket_updated",
        data={"ticket": TicketRead.model_validate(out_obj).model_dump(mode="json")},
    )
    return out_obj



# =========================================================================
# DEDICATED LIFECYCLE TRANSITION ENDPOINTS (Task 5: Server-Side Enforcement)
# =========================================================================

@router.post("/{ticket_id}/accept", response_model=TicketRead)
async def accept_ticket(
    ticket_id: str,
    payload: Optional[TicketAcceptRequest] = None,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    1. open -> in_progress:
       The assignee accepts, or any member claims an unassigned ticket (sets assignee).
    2. passed -> in_progress:
       Only the intended recipient of the handoff can accept the ticket.
    """
    ticket = await _get_authorized_ticket(ticket_id, current_user, db)
    old_status = ticket.status.lower()

    if old_status not in ("open", "passed"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "invalid_transition", "message": f"Cannot accept ticket in '{ticket.status}' status. Only open or passed tickets can be accepted."}
        )

    sender_id = None
    if old_status == "passed":
        # Look up the latest handoff to determine the intended recipient and original sender
        h_res = await db.execute(
            select(TicketHandoff)
            .where(TicketHandoff.ticket_id == ticket.id)
            .order_by(desc(TicketHandoff.created_at))
        )
        latest_handoff = h_res.scalars().first()
        intended_recipient_id = latest_handoff.to_user_id if latest_handoff else ticket.assignee_id
        sender_id = latest_handoff.from_user_id if latest_handoff else None

        if not intended_recipient_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "no_handoff_recipient", "message": "No intended recipient found for this handoff."}
            )

        if current_user.id != intended_recipient_id:
            if sender_id and current_user.id == sender_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={"code": "sender_cannot_accept", "message": "You cannot accept a ticket that you passed. The recipient must accept it."}
                )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "not_handoff_recipient", "message": "Only the intended recipient of this handoff can accept this ticket."}
            )

        new_assignee_id = current_user.id
    else:
        # old_status == "open"
        if ticket.assignee_id and ticket.assignee_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "not_assignee", "message": "Only the assigned user can accept this ticket."}
            )
        new_assignee_id = ticket.assignee_id or current_user.id

    current_v = ticket.version or 0
    if payload and payload.version is not None and payload.version != current_v:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "concurrency_conflict", "message": "Ticket has been modified by another concurrent action. Please refresh and retry."}
        )

    new_v = current_v + 1
    now = datetime.now(timezone.utc)

    res_update = await db.execute(
        update(Ticket)
        .where(Ticket.id == ticket.id, Ticket.version == ticket.version)
        .values(status="in_progress", assignee_id=new_assignee_id, version=new_v, updated_at=now)
    )
    if res_update.rowcount == 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "concurrency_conflict", "message": "Ticket has been modified by another concurrent action. Please refresh and retry."}
        )

    act = TicketActivity(
        ticket_id=ticket.id,
        actor_id=current_user.id,
        action="accepted",
        old_value=old_status,
        new_value=f"in_progress (Assignee: {current_user.full_name or current_user.email})",
        created_at=now,
    )
    db.add(act)

    audit = AuditLog(
        organization_id=ticket.project.organization_id,
        actor_id=current_user.id,
        action="ticket_accepted",
        details=json.dumps({
            "ticket_id": ticket.id,
            "ticket_number": ticket.ticket_number,
            "kind": "accept",
            "actor_id": current_user.id,
            "assignee_id": new_assignee_id,
            "old_status": old_status,
            "new_status": "in_progress",
        })
    )
    db.add(audit)

    notif_recipients = [ticket.reporter_id]
    if old_status == "passed" and sender_id and sender_id not in notif_recipients:
        notif_recipients.append(sender_id)
    notif_recipients = [uid for uid in notif_recipients if uid and uid != current_user.id]

    await _send_transition_notifications(
        db=db,
        ticket=ticket,
        actor=current_user,
        action="accepted",
        title="Ticket Accepted",
        body=f"{current_user.full_name or current_user.email} accepted ticket {ticket.ticket_number}",
        recipient_ids=notif_recipients,
    )

    await db.commit()

    refreshed = await db.execute(
        select(Ticket).options(
            selectinload(Ticket.assignee),
            selectinload(Ticket.reporter),
            selectinload(Ticket.project),
            selectinload(Ticket.comments).selectinload(TicketComment.author),
            selectinload(Ticket.comments).selectinload(TicketComment.attachments).selectinload(TicketAttachment.uploader),
            selectinload(Ticket.attachments).selectinload(TicketAttachment.uploader),
            selectinload(Ticket.activities).selectinload(TicketActivity.actor),
            selectinload(Ticket.handoffs).selectinload(TicketHandoff.from_user),
            selectinload(Ticket.handoffs).selectinload(TicketHandoff.to_user),
        ).where(Ticket.id == ticket.id)
    )
    out_obj = refreshed.scalars().first()

    workspace_id = ticket.project.organization_id
    await ws_manager.broadcast(
        workspace_id=workspace_id,
        event_type="ticket_updated",
        data={"ticket": TicketRead.model_validate(out_obj).model_dump(mode="json")},
    )

    return out_obj



@router.post("/{ticket_id}/pass", response_model=TicketRead)
async def pass_ticket(
    ticket_id: str,
    payload: TicketPassRequest,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    2. in_progress -> passed:
       Only current assignee, to another active member of the same org (not themselves, not suspended),
       with a required message (10 to 1000 characters) and optional attachment ids.
       Target becomes assignee, ticket_handoffs row (kind 'pass') written, message posted as comment.
    """
    check_handoff_rate_limit(current_user.id)
    ticket = await _get_authorized_ticket(ticket_id, current_user, db)
    old_status = ticket.status.lower()

    if old_status not in ("in_progress", "wip"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "invalid_transition", "message": f"Cannot pass ticket in '{ticket.status}' status. Only tickets in progress can be passed."}
        )

    if ticket.assignee_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "not_assignee", "message": "Only the current assignee can pass this ticket."}
        )

    target_id = payload.to_user_id.strip()
    if target_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "cannot_pass_to_self", "message": "Cannot pass a ticket to yourself."}
        )

    org_id = ticket.project.organization_id
    target_res = await db.execute(
        select(User)
        .join(Membership, Membership.user_id == User.id)
        .where(
            User.id == target_id,
            Membership.organization_id == org_id,
        )
    )
    target_user = target_res.scalars().first()
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "invalid_target_user", "message": "Target user does not belong to this organization."}
        )
    if not target_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "target_user_inactive", "message": "Target user account is inactive."}
        )

    mem_res = await db.execute(
        select(Membership).where(
            Membership.user_id == target_id,
            Membership.organization_id == org_id,
        )
    )
    membership = mem_res.scalars().first()
    if not membership or membership.status == "suspended":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "target_user_suspended", "message": "Target user is suspended in this organization."}
        )

    msg = (payload.message or "").strip()
    if len(msg) < 10 or len(msg) > 1000:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "invalid_message", "message": "Handoff message must be between 10 and 1000 characters."}
        )

    current_v = ticket.version or 0
    if payload.version is not None and payload.version != current_v:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "concurrency_conflict", "message": "Ticket has been modified by another concurrent action. Please refresh and retry."}
        )

    new_v = current_v + 1
    now = datetime.now(timezone.utc)

    res_update = await db.execute(
        update(Ticket)
        .where(Ticket.id == ticket.id, Ticket.version == ticket.version)
        .values(status="passed", assignee_id=target_user.id, version=new_v, updated_at=now)
    )
    if res_update.rowcount == 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "concurrency_conflict", "message": "Ticket has been modified by another concurrent action. Please refresh and retry."}
        )

    handoff = TicketHandoff(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        from_user_id=current_user.id,
        to_user_id=target_user.id,
        message=msg,
        status_before=old_status,
        status_after="passed",
        kind="pass",
        created_at=now,
    )
    db.add(handoff)

    comment = TicketComment(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        author_id=current_user.id,
        content=msg,
        created_at=now,
    )
    db.add(comment)
    await db.flush()

    if payload.attachment_ids:
        for att_id in payload.attachment_ids:
            att_res = await db.execute(
                select(TicketAttachment).where(TicketAttachment.id == att_id, TicketAttachment.ticket_id == ticket.id)
            )
            att_obj = att_res.scalars().first()
            if att_obj:
                att_obj.comment_id = comment.id

    act = TicketActivity(
        ticket_id=ticket.id,
        actor_id=current_user.id,
        action="passed",
        old_value=old_status,
        new_value=f"passed to {target_user.full_name or target_user.email}: {msg[:80]}",
        created_at=now,
    )
    db.add(act)

    audit = AuditLog(
        organization_id=org_id,
        actor_id=current_user.id,
        action="ticket_passed",
        details=json.dumps({
            "ticket_id": ticket.id,
            "ticket_number": ticket.ticket_number,
            "kind": "pass",
            "from_user_id": current_user.id,
            "to_user_id": target_user.id,
            "message": msg,
        })
    )
    db.add(audit)

    await _send_transition_notifications(
        db=db,
        ticket=ticket,
        actor=current_user,
        action="passed",
        title="Ticket Passed to You",
        body=f"{current_user.full_name or current_user.email} passed ticket {ticket.ticket_number} to you: {msg[:100]}",
        recipient_ids=[target_user.id, ticket.reporter_id],
    )

    await _try_send_transition_email(
        to_email=target_user.email,
        subject=f"Ticket {ticket.ticket_number} Passed to You",
        message=f"{current_user.full_name or current_user.email} passed ticket {ticket.ticket_number} to you.\n\nMessage: {msg}"
    )

    await db.commit()

    refreshed = await db.execute(
        select(Ticket).options(
            selectinload(Ticket.assignee),
            selectinload(Ticket.reporter),
            selectinload(Ticket.project),
            selectinload(Ticket.comments).selectinload(TicketComment.author),
            selectinload(Ticket.comments).selectinload(TicketComment.attachments).selectinload(TicketAttachment.uploader),
            selectinload(Ticket.attachments).selectinload(TicketAttachment.uploader),
            selectinload(Ticket.activities).selectinload(TicketActivity.actor),
            selectinload(Ticket.handoffs).selectinload(TicketHandoff.from_user),
            selectinload(Ticket.handoffs).selectinload(TicketHandoff.to_user),
        ).where(Ticket.id == ticket.id)
    )
    out_obj = refreshed.scalars().first()

    await ws_manager.broadcast(
        workspace_id=org_id,
        event_type="ticket_updated",
        data={"ticket": TicketRead.model_validate(out_obj).model_dump(mode="json")},
    )
    await ws_manager.broadcast(
        workspace_id=org_id,
        event_type="ticket_handoff",
        data={
            "ticket_id": ticket.id,
            "kind": "pass",
            "from_user_id": current_user.id,
            "to_user_id": target_user.id,
            "status_before": old_status,
            "status_after": "passed",
        },
    )

    return out_obj



@router.post("/{ticket_id}/hold", response_model=TicketRead)
async def hold_ticket(
    ticket_id: str,
    payload: TicketHoldRequest,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    4. in_progress -> on_hold:
       Only current assignee, with required reason.
    """
    ticket = await _get_authorized_ticket(ticket_id, current_user, db)
    old_status = ticket.status.lower()

    if old_status not in ("in_progress", "wip"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "invalid_transition", "message": f"Cannot place ticket in '{ticket.status}' status on hold. Only in-progress tickets can be placed on hold."}
        )

    if ticket.assignee_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "not_assignee", "message": "Only the current assignee can place this ticket on hold."}
        )

    reason = (payload.reason or "").strip()
    if not reason:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "missing_reason", "message": "A reason is required when placing a ticket on hold."}
        )

    current_v = ticket.version or 0
    if payload.version is not None and payload.version != current_v:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "concurrency_conflict", "message": "Ticket has been modified by another concurrent action. Please refresh and retry."}
        )

    new_v = current_v + 1
    now = datetime.now(timezone.utc)

    res_update = await db.execute(
        update(Ticket)
        .where(Ticket.id == ticket.id, Ticket.version == ticket.version)
        .values(status="on_hold", version=new_v, updated_at=now)
    )
    if res_update.rowcount == 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "concurrency_conflict", "message": "Ticket has been modified by another concurrent action. Please refresh and retry."}
        )

    act = TicketActivity(
        ticket_id=ticket.id,
        actor_id=current_user.id,
        action="placed_on_hold",
        old_value=old_status,
        new_value=f"on_hold (Reason: {reason})",
        created_at=now,
    )
    db.add(act)

    audit = AuditLog(
        organization_id=ticket.project.organization_id,
        actor_id=current_user.id,
        action="ticket_on_hold",
        details=json.dumps({
            "ticket_id": ticket.id,
            "ticket_number": ticket.ticket_number,
            "kind": "hold",
            "reason": reason,
            "actor_id": current_user.id,
        })
    )
    db.add(audit)

    await _send_transition_notifications(
        db=db,
        ticket=ticket,
        actor=current_user,
        action="hold",
        title="Ticket Placed on Hold",
        body=f"Ticket {ticket.ticket_number} placed on hold: {reason}",
        recipient_ids=[ticket.reporter_id],
    )

    await db.commit()

    refreshed = await db.execute(
        select(Ticket).options(
            selectinload(Ticket.assignee),
            selectinload(Ticket.reporter),
            selectinload(Ticket.project),
            selectinload(Ticket.comments).selectinload(TicketComment.author),
            selectinload(Ticket.comments).selectinload(TicketComment.attachments).selectinload(TicketAttachment.uploader),
            selectinload(Ticket.attachments).selectinload(TicketAttachment.uploader),
            selectinload(Ticket.activities).selectinload(TicketActivity.actor),
            selectinload(Ticket.handoffs).selectinload(TicketHandoff.from_user),
            selectinload(Ticket.handoffs).selectinload(TicketHandoff.to_user),
        ).where(Ticket.id == ticket.id)
    )
    out_obj = refreshed.scalars().first()

    workspace_id = ticket.project.organization_id
    await ws_manager.broadcast(
        workspace_id=workspace_id,
        event_type="ticket_updated",
        data={"ticket": TicketRead.model_validate(out_obj).model_dump(mode="json")},
    )

    return out_obj



@router.post("/{ticket_id}/resume", response_model=TicketRead)
async def resume_ticket(
    ticket_id: str,
    payload: Optional[TicketResumeRequest] = None,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    on_hold -> in_progress: only the assignee.
    (passed tickets must use /accept to ensure explicit acceptance by the recipient).
    """
    ticket = await _get_authorized_ticket(ticket_id, current_user, db)
    old_status = ticket.status.lower()

    if old_status == "passed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "acceptance_required", "message": "Ticket is awaiting recipient acceptance. Please accept the ticket instead of resuming."}
        )
    elif old_status == "on_hold":
        if ticket.assignee_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "not_assignee", "message": "Only the assigned user can resume work from on hold."}
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "invalid_transition", "message": f"Cannot resume ticket in '{ticket.status}' status. Only on-hold tickets can be resumed."}
        )

    current_v = ticket.version or 0
    if payload and payload.version is not None and payload.version != current_v:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "concurrency_conflict", "message": "Ticket has been modified by another concurrent action. Please refresh and retry."}
        )

    new_v = current_v + 1
    now = datetime.now(timezone.utc)

    res_update = await db.execute(
        update(Ticket)
        .where(Ticket.id == ticket.id, Ticket.version == ticket.version)
        .values(status="in_progress", version=new_v, updated_at=now)
    )
    if res_update.rowcount == 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "concurrency_conflict", "message": "Ticket has been modified by another concurrent action. Please refresh and retry."}
        )

    act = TicketActivity(
        ticket_id=ticket.id,
        actor_id=current_user.id,
        action="resumed",
        old_value=old_status,
        new_value="in_progress",
        created_at=now,
    )
    db.add(act)

    audit = AuditLog(
        organization_id=ticket.project.organization_id,
        actor_id=current_user.id,
        action="ticket_resumed",
        details=json.dumps({
            "ticket_id": ticket.id,
            "ticket_number": ticket.ticket_number,
            "kind": "resume",
            "old_status": old_status,
            "new_status": "in_progress",
            "actor_id": current_user.id,
        })
    )
    db.add(audit)

    await _send_transition_notifications(
        db=db,
        ticket=ticket,
        actor=current_user,
        action="resumed",
        title="Ticket Resumed",
        body=f"{current_user.full_name or current_user.email} resumed ticket {ticket.ticket_number}",
        recipient_ids=[ticket.reporter_id],
    )

    await db.commit()

    refreshed = await db.execute(
        select(Ticket).options(
            selectinload(Ticket.assignee),
            selectinload(Ticket.reporter),
            selectinload(Ticket.project),
            selectinload(Ticket.comments).selectinload(TicketComment.author),
            selectinload(Ticket.comments).selectinload(TicketComment.attachments).selectinload(TicketAttachment.uploader),
            selectinload(Ticket.attachments).selectinload(TicketAttachment.uploader),
            selectinload(Ticket.activities).selectinload(TicketActivity.actor),
            selectinload(Ticket.handoffs).selectinload(TicketHandoff.from_user),
            selectinload(Ticket.handoffs).selectinload(TicketHandoff.to_user),
        ).where(Ticket.id == ticket.id)
    )
    out_obj = refreshed.scalars().first()

    workspace_id = ticket.project.organization_id
    await ws_manager.broadcast(
        workspace_id=workspace_id,
        event_type="ticket_updated",
        data={"ticket": TicketRead.model_validate(out_obj).model_dump(mode="json")},
    )

    return out_obj



@router.post("/{ticket_id}/close", response_model=TicketRead)
async def close_ticket(
    ticket_id: str,
    payload: Optional[TicketCloseRequest] = None,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    5. CLOSING:
       Only the organization owner, and only when:
       (a) ticket is currently passed to the owner (status passed and owner is assignee), or
       (b) owner is the assignee in in_progress.
       (7) If status is 'resolved', only owner can close or reopen.
       Closing from any other state or by anyone else returns 409 or 403 with clear message.
    """
    ticket = await _get_authorized_ticket(ticket_id, current_user, db)
    org_id = ticket.project.organization_id

    if not is_org_owner(current_user, org_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "owner_required", "message": "Only the organization owner can close tickets."}
        )

    old_status = ticket.status.lower()
    is_passed_to_owner = (old_status == "passed" and ticket.assignee_id == current_user.id)
    is_active_in_progress = (old_status in ("in_progress", "wip"))
    is_resolved = (old_status == "resolved")

    if not (is_passed_to_owner or is_active_in_progress or is_resolved):
        if old_status == "open":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "invalid_closing_state", "message": "Cannot close an open ticket. Ticket must be in progress or passed to the owner before closing."}
            )
        elif old_status == "passed" and ticket.assignee_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "not_passed_to_owner", "message": "Owner can only close a passed ticket if it is currently passed to the owner."}
            )
        elif old_status == "closed":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "already_closed", "message": "Ticket is already closed."}
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "invalid_closing_state", "message": f"Cannot close ticket in '{ticket.status}' status."}
            )

    current_v = ticket.version or 0
    if payload and payload.version is not None and payload.version != current_v:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "concurrency_conflict", "message": "Ticket has been modified by another concurrent action. Please refresh and retry."}
        )

    close_msg = ""
    if payload:
        close_msg = (payload.message or payload.resolution_summary or "").strip()
    if not close_msg:
        close_msg = "Closed by organization owner"

    new_v = current_v + 1
    now = datetime.now(timezone.utc)

    res_update = await db.execute(
        update(Ticket)
        .where(Ticket.id == ticket.id, Ticket.version == ticket.version)
        .values(status="closed", version=new_v, updated_at=now)
    )
    if res_update.rowcount == 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "concurrency_conflict", "message": "Ticket has been modified by another concurrent action. Please refresh and retry."}
        )

    act = TicketActivity(
        ticket_id=ticket.id,
        actor_id=current_user.id,
        action="closed",
        old_value=old_status,
        new_value=f"closed: {close_msg}",
        created_at=now,
    )
    db.add(act)

    audit = AuditLog(
        organization_id=org_id,
        actor_id=current_user.id,
        action="ticket_closed",
        details=json.dumps({
            "ticket_id": ticket.id,
            "ticket_number": ticket.ticket_number,
            "kind": "close",
            "old_status": old_status,
            "new_status": "closed",
            "reason": close_msg,
            "actor_id": current_user.id,
        })
    )
    db.add(audit)

    await _send_transition_notifications(
        db=db,
        ticket=ticket,
        actor=current_user,
        action="closed",
        title="Ticket Closed",
        body=f"Ticket {ticket.ticket_number} was closed: {close_msg}",
        recipient_ids=[ticket.reporter_id, ticket.assignee_id],
    )

    await db.commit()

    refreshed = await db.execute(
        select(Ticket).options(
            selectinload(Ticket.assignee),
            selectinload(Ticket.reporter),
            selectinload(Ticket.project),
            selectinload(Ticket.comments).selectinload(TicketComment.author),
            selectinload(Ticket.comments).selectinload(TicketComment.attachments).selectinload(TicketAttachment.uploader),
            selectinload(Ticket.attachments).selectinload(TicketAttachment.uploader),
            selectinload(Ticket.activities).selectinload(TicketActivity.actor),
            selectinload(Ticket.handoffs).selectinload(TicketHandoff.from_user),
            selectinload(Ticket.handoffs).selectinload(TicketHandoff.to_user),
        ).where(Ticket.id == ticket.id)
    )
    out_obj = refreshed.scalars().first()

    await ws_manager.broadcast(
        workspace_id=org_id,
        event_type="ticket_updated",
        data={"ticket": TicketRead.model_validate(out_obj).model_dump(mode="json")},
    )

    return out_obj



@router.post("/{ticket_id}/reopen", response_model=TicketRead)
async def reopen_ticket(
    ticket_id: str,
    payload: TicketReopenRequest,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    5. The owner may reopen (closed -> open) with a required message.
    (7) If status is 'resolved', only owner can close or reopen.
    """
    ticket = await _get_authorized_ticket(ticket_id, current_user, db)
    org_id = ticket.project.organization_id

    if not is_org_owner(current_user, org_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "owner_required", "message": "Only the organization owner can reopen tickets."}
        )

    old_status = ticket.status.lower()
    if old_status not in ("closed", "resolved"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "invalid_reopen_state", "message": f"Cannot reopen ticket in '{ticket.status}' status. Only closed or resolved tickets can be reopened."}
        )

    reopen_msg = (payload.message or payload.reason or payload.classification or "").strip()
    if not reopen_msg:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "missing_message", "message": "A message or reason is required to reopen a ticket."}
        )

    current_v = ticket.version or 0
    if payload.version is not None and payload.version != current_v:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "concurrency_conflict", "message": "Ticket has been modified by another concurrent action. Please refresh and retry."}
        )

    new_v = current_v + 1
    now = datetime.now(timezone.utc)

    res_update = await db.execute(
        update(Ticket)
        .where(Ticket.id == ticket.id, Ticket.version == ticket.version)
        .values(status="open", version=new_v, updated_at=now)
    )
    if res_update.rowcount == 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "concurrency_conflict", "message": "Ticket has been modified by another concurrent action. Please refresh and retry."}
        )

    act = TicketActivity(
        ticket_id=ticket.id,
        actor_id=current_user.id,
        action="reopened",
        old_value=old_status,
        new_value=f"open (Reason: {reopen_msg})",
        created_at=now,
    )
    db.add(act)

    audit = AuditLog(
        organization_id=org_id,
        actor_id=current_user.id,
        action="ticket_reopened",
        details=json.dumps({
            "ticket_id": ticket.id,
            "ticket_number": ticket.ticket_number,
            "kind": "reopen",
            "old_status": old_status,
            "new_status": "open",
            "reason": reopen_msg,
            "actor_id": current_user.id,
        })
    )
    db.add(audit)

    await _send_transition_notifications(
        db=db,
        ticket=ticket,
        actor=current_user,
        action="reopened",
        title="Ticket Reopened",
        body=f"Ticket {ticket.ticket_number} was reopened by owner: {reopen_msg}",
        recipient_ids=[ticket.reporter_id, ticket.assignee_id],
    )

    await db.commit()

    refreshed = await db.execute(
        select(Ticket).options(
            selectinload(Ticket.assignee),
            selectinload(Ticket.reporter),
            selectinload(Ticket.project),
            selectinload(Ticket.comments).selectinload(TicketComment.author),
            selectinload(Ticket.comments).selectinload(TicketComment.attachments).selectinload(TicketAttachment.uploader),
            selectinload(Ticket.attachments).selectinload(TicketAttachment.uploader),
            selectinload(Ticket.activities).selectinload(TicketActivity.actor),
            selectinload(Ticket.handoffs).selectinload(TicketHandoff.from_user),
            selectinload(Ticket.handoffs).selectinload(TicketHandoff.to_user),
        ).where(Ticket.id == ticket.id)
    )
    out_obj = refreshed.scalars().first()

    await ws_manager.broadcast(
        workspace_id=org_id,
        event_type="ticket_updated",
        data={"ticket": TicketRead.model_validate(out_obj).model_dump(mode="json")},
    )

    return out_obj



@router.post("/{ticket_id}/reassign", response_model=TicketRead)
async def reassign_ticket_endpoint(
    ticket_id: str,
    payload: TicketOwnerReassignRequest,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    6. OWNER REASSIGN:
       The owner (not admins or members) can reassign any ticket that is not closed to another
       active member, even if someone else holds it, with a REQUIRED message (10 to 1000 characters).
       The new person becomes the assignee, status becomes passed, a handoff row with kind 'reassign'
       is written, the message is posted as a comment, and previous holder and new assignee are notified.
    """
    check_handoff_rate_limit(current_user.id)
    ticket = await _get_authorized_ticket(ticket_id, current_user, db)
    org_id = ticket.project.organization_id

    if not is_org_owner(current_user, org_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "owner_required", "message": "Only the organization owner can reassign tickets."}
        )

    old_status = ticket.status.lower()
    if old_status == "closed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "ticket_closed", "message": "Cannot reassign a closed ticket."}
        )

    target_id = payload.assignee_id.strip()
    target_res = await db.execute(
        select(User)
        .join(Membership, Membership.user_id == User.id)
        .where(
            User.id == target_id,
            Membership.organization_id == org_id,
        )
    )
    target_user = target_res.scalars().first()
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "invalid_target_user", "message": "Target user does not belong to this organization."}
        )
    if not target_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "target_user_inactive", "message": "Target user account is inactive."}
        )

    mem_res = await db.execute(
        select(Membership).where(
            Membership.user_id == target_id,
            Membership.organization_id == org_id,
        )
    )
    membership = mem_res.scalars().first()
    if not membership or membership.status == "suspended":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "target_user_suspended", "message": "Target user is suspended in this organization."}
        )

    msg = (payload.message or "").strip()
    if len(msg) < 10 or len(msg) > 1000:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "invalid_message", "message": "Reassignment message must be between 10 and 1000 characters."}
        )

    current_v = ticket.version or 0
    if payload.version is not None and payload.version != current_v:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "concurrency_conflict", "message": "Ticket has been modified by another concurrent action. Please refresh and retry."}
        )

    prev_holder_id = ticket.assignee_id
    new_v = current_v + 1
    now = datetime.now(timezone.utc)

    res_update = await db.execute(
        update(Ticket)
        .where(Ticket.id == ticket.id, Ticket.version == ticket.version)
        .values(status="passed", assignee_id=target_user.id, version=new_v, updated_at=now)
    )
    if res_update.rowcount == 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "concurrency_conflict", "message": "Ticket has been modified by another concurrent action. Please refresh and retry."}
        )

    handoff = TicketHandoff(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        from_user_id=current_user.id,
        to_user_id=target_user.id,
        message=msg,
        status_before=old_status,
        status_after="passed",
        kind="reassign",
        created_at=now,
    )
    db.add(handoff)

    comment = TicketComment(
        id=str(uuid.uuid4()),
        ticket_id=ticket.id,
        author_id=current_user.id,
        content=msg,
        created_at=now,
    )
    db.add(comment)
    await db.flush()

    if payload.attachment_ids:
        for att_id in payload.attachment_ids:
            att_res = await db.execute(
                select(TicketAttachment).where(TicketAttachment.id == att_id, TicketAttachment.ticket_id == ticket.id)
            )
            att_obj = att_res.scalars().first()
            if att_obj:
                att_obj.comment_id = comment.id

    act = TicketActivity(
        ticket_id=ticket.id,
        actor_id=current_user.id,
        action="owner_reassigned",
        old_value=old_status,
        new_value=f"Reassigned to {target_user.full_name or target_user.email}: {msg[:80]}",
        created_at=now,
    )
    db.add(act)

    audit = AuditLog(
        organization_id=org_id,
        actor_id=current_user.id,
        action="ticket_reassigned",
        details=json.dumps({
            "ticket_id": ticket.id,
            "ticket_number": ticket.ticket_number,
            "kind": "reassign",
            "from_user_id": current_user.id,
            "prev_holder_id": prev_holder_id,
            "to_user_id": target_user.id,
            "message": msg,
        })
    )
    db.add(audit)

    await _send_transition_notifications(
        db=db,
        ticket=ticket,
        actor=current_user,
        action="reassigned",
        title="Ticket Reassigned",
        body=f"Owner reassigned ticket {ticket.ticket_number} to {target_user.full_name or target_user.email}: {msg[:100]}",
        recipient_ids=[prev_holder_id, target_user.id, ticket.reporter_id],
    )

    await _try_send_transition_email(
        to_email=target_user.email,
        subject=f"Ticket {ticket.ticket_number} Reassigned to You",
        message=f"Owner reassigned ticket {ticket.ticket_number} to you.\n\nMessage: {msg}"
    )

    await db.commit()

    refreshed = await db.execute(
        select(Ticket).options(
            selectinload(Ticket.assignee),
            selectinload(Ticket.reporter),
            selectinload(Ticket.project),
            selectinload(Ticket.comments).selectinload(TicketComment.author),
            selectinload(Ticket.comments).selectinload(TicketComment.attachments).selectinload(TicketAttachment.uploader),
            selectinload(Ticket.attachments).selectinload(TicketAttachment.uploader),
            selectinload(Ticket.activities).selectinload(TicketActivity.actor),
            selectinload(Ticket.handoffs).selectinload(TicketHandoff.from_user),
            selectinload(Ticket.handoffs).selectinload(TicketHandoff.to_user),
        ).where(Ticket.id == ticket.id)
    )
    out_obj = refreshed.scalars().first()

    await ws_manager.broadcast(
        workspace_id=org_id,
        event_type="ticket_updated",
        data={"ticket": TicketRead.model_validate(out_obj).model_dump(mode="json")},
    )
    await ws_manager.broadcast(
        workspace_id=org_id,
        event_type="ticket_handoff",
        data={
            "ticket_id": ticket.id,
            "kind": "reassign",
            "from_user_id": current_user.id,
            "to_user_id": target_user.id,
            "status_before": old_status,
            "status_after": "passed",
        },
    )

    return out_obj


@router.get("/{ticket_id}/handoffs", response_model=List[TicketHandoffRead])
async def list_ticket_handoffs(
    ticket_id: str,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """List all handoff records for this ticket with attached messages and files."""
    ticket = await _get_authorized_ticket(ticket_id, current_user, db)

    res = await db.execute(
        select(TicketHandoff)
        .options(
            selectinload(TicketHandoff.from_user),
            selectinload(TicketHandoff.to_user),
        )
        .where(TicketHandoff.ticket_id == ticket.id)
        .order_by(desc(TicketHandoff.created_at))
    )
    handoffs = res.scalars().all()

    # Load comments and attachments for this ticket to correlate attachments
    comments_res = await db.execute(
        select(TicketComment)
        .options(
            selectinload(TicketComment.attachments).selectinload(TicketAttachment.uploader)
        )
        .where(TicketComment.ticket_id == ticket.id)
    )
    comments = comments_res.scalars().all()

    results = []
    for h in handoffs:
        matched_atts = []
        for c in comments:
            if c.author_id == h.from_user_id and c.content == h.message:
                matched_atts = c.attachments
                break

        results.append(
            TicketHandoffRead(
                id=h.id,
                ticket_id=h.ticket_id,
                from_user_id=h.from_user_id,
                to_user_id=h.to_user_id,
                from_user=UserRead.model_validate(h.from_user) if h.from_user else None,
                to_user=UserRead.model_validate(h.to_user) if h.to_user else None,
                message=h.message,
                status_before=h.status_before,
                status_after=h.status_after,
                kind=h.kind or "pass",
                created_at=h.created_at,
                attachments=[TicketAttachmentRead.model_validate(a) for a in matched_atts],
            )
        )
    return results


