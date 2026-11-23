"""
Tasks router — Enterprise Grade, Multi-Tenant Database Driven Implementation.
Enforces strict organization isolation, active JWT authentication, and real-time WebSocket broadcasting.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import List, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status, Request
from sqlalchemy import select, func, desc, or_, and_
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.database import get_db
from backend.app.dependencies import get_current_active_user
from backend.app.models import (
    Task, Project, User, ProjectActivity, TaskComment, TaskAttachment,
    TaskActivity, Ticket, TicketActivity, TicketComment, TicketAttachment,
    TicketHandoff, AuditLog, Membership
)
from backend.app.enums import TaskStatus, TaskPriority, TaskType
from backend.app.core.websockets import ws_manager, authenticate_ws
from backend.app.services.metrics import get_my_tasks_stats
from backend.app.schemas import TicketRead
from backend.app.routes.tasks_schemas import (
    TaskCreate,
    TaskUpdate,
    TaskStatusChange,
    TaskRead,
    TaskListResponse,
    TaskCommentCreate,
    TaskCommentUpdate,
    TaskCommentRead,
    TaskAttachmentCreate,
    TaskAttachmentRead,
    TaskActivityRead,
    TaskActivityListResponse,
    LinkTicketRequest,
    ActorSchema,
    TaskPermissions,
    TaskTicketListResponse,
)

router = APIRouter()

ALLOWED_TASK_UPDATE_FIELDS = {
    "title", "description", "status", "priority", "task_type", "type",
    "story_points", "due_date", "project_id", "assignee_id",
    "assigned_to_id", "reporter_id", "assigner_id"
}

TASK_DETAIL_FIELDS = {
    "title", "description", "priority", "task_type", "type",
    "story_points", "due_date", "project_id", "assignee_id",
    "assigned_to_id", "reporter_id", "assigner_id"
}

TASK_STATUS_FIELDS = {"status"}


def _compute_task_permissions(t: Task, current_user: Optional[User]) -> TaskPermissions:
    if not current_user:
        return TaskPermissions(can_edit_details=False, can_change_status=False, can_delete=False)
    user_role = (current_user.role or "").lower().strip()
    is_owner_or_admin = user_role in ("owner", "admin")
    is_creator = (current_user.id == t.reporter_id)
    is_assignee = (current_user.id == t.assignee_id)

    can_edit_details = is_owner_or_admin or is_creator
    can_change_status = is_owner_or_admin or is_creator or is_assignee
    can_delete = is_owner_or_admin or is_creator

    return TaskPermissions(
        can_edit_details=can_edit_details,
        can_change_status=can_change_status,
        can_delete=can_delete,
    )


def _sanitize_value(val: Optional[any], field_name: Optional[str] = None) -> Optional[str]:
    if val is None:
        return None
    val_str = str(val)
    if field_name and any(k in field_name.lower() for k in ("password", "token", "secret", "key", "auth")):
        return "[REDACTED]"
    if len(val_str) > 500:
        return val_str[:500]
    return val_str


def _get_actor_display_name(u: Optional[User]) -> str:
    if not u:
        return "Deleted user"
    return (u.display_name or u.full_name or "").strip() or "Deleted user"


def _build_actor_schema(u: Optional[User], fallback_id: Optional[str] = None) -> Optional[ActorSchema]:
    if not u:
        if fallback_id:
            return ActorSchema(
                id=fallback_id,
                full_name="Deleted user",
                display_name="Deleted user",
                avatar_url=None,
                role=None,
            )
        return None
    name = _get_actor_display_name(u)
    return ActorSchema(
        id=u.id,
        full_name=name,
        display_name=u.display_name or name,
        avatar_url=u.avatar_url,
        role=u.role,
    )


def _format_task_activity_summary(
    action: str,
    field_name: Optional[str],
    old_val: Optional[str],
    new_val: Optional[str],
    actor_name: str,
    meta: Optional[dict] = None,
) -> str:
    meta = meta or {}
    if action == "created":
        return f"{actor_name} created the task"
    elif action == "status_changed":
        return f"{actor_name} changed status from '{old_val or 'none'}' to '{new_val or 'none'}'"
    elif action == "assignee_changed":
        old_label = meta.get("old_assignee_name") or old_val or "Unassigned"
        new_label = meta.get("new_assignee_name") or new_val or "Unassigned"
        return f"{actor_name} changed assignee from '{old_label}' to '{new_label}'"
    elif action == "field_updated":
        field_display = (field_name or "field").replace("_", " ")
        return f"{actor_name} updated {field_display}"
    elif action == "comment_added":
        return f"{actor_name} added a comment"
    elif action == "comment_edited":
        return f"{actor_name} edited a comment"
    elif action == "comment_deleted":
        return f"{actor_name} deleted a comment"
    elif action == "attachment_added":
        fname = meta.get("filename") or new_val or "file"
        return f"{actor_name} attached {fname}"
    elif action == "attachment_removed":
        fname = meta.get("filename") or old_val or "file"
        return f"{actor_name} removed attachment {fname}"
    elif action == "ticket_linked":
        tnum = meta.get("ticket_number") or new_val or "ticket"
        return f"{actor_name} linked ticket {tnum}"
    elif action == "ticket_unlinked":
        tnum = meta.get("ticket_number") or old_val or "ticket"
        return f"{actor_name} unlinked ticket {tnum}"
    elif action == "task_deleted":
        return f"{actor_name} deleted the task"
    return f"{actor_name} performed {action.replace('_', ' ')}"


def _format_task_activity_read(act: TaskActivity, actor: Optional[User]) -> TaskActivityRead:
    actor_name = _get_actor_display_name(actor)
    old_display = act.old_value
    new_display = act.new_value
    if act.action == "assignee_changed":
        meta = act.meta or {}
        old_display = meta.get("old_assignee_name") or "Unassigned"
        new_display = meta.get("new_assignee_name") or "Unassigned"
    return TaskActivityRead(
        id=act.id,
        task_id=act.task_id,
        organization_id=act.organization_id,
        actor_id=act.actor_id,
        actor_name=actor_name,
        actor_avatar=actor.avatar_url if actor else None,
        action=act.action,
        field_name=act.field_name,
        old_value=old_display,
        new_value=new_display,
        summary=_format_task_activity_summary(
            act.action, act.field_name, old_display, new_display, actor_name, act.meta
        ),
        meta=act.meta,
        created_at=act.created_at,
    )


def _format_task_read(t: Task, current_user: Optional[User] = None) -> TaskRead:
    assignee_actor = _build_actor_schema(getattr(t, "assignee", None), getattr(t, "assignee_id", None))
    reporter_actor = _build_actor_schema(getattr(t, "reporter", None), getattr(t, "reporter_id", None))
    tickets_list = getattr(t, "tickets", None) or []
    t_count = len(tickets_list)
    open_t_count = sum(
        1 for tk in tickets_list
        if (getattr(tk, "status", None) or "").lower() not in ("closed", "resolved")
    )
    return TaskRead(
        id=t.id,
        task_number=t.task_number,
        title=t.title,
        description=t.description,
        status=t.status,
        priority=t.priority,
        task_type=getattr(t, "task_type", "feature") or "feature",
        story_points=t.story_points,
        due_date=t.due_date,
        project_id=t.project_id,
        assignee_id=t.assignee_id,
        assigned_to_id=t.assignee_id,
        reporter_id=t.reporter_id,
        assigner_id=t.reporter_id,
        organization_id=t.organization_id,
        created_at=t.created_at,
        updated_at=t.updated_at,
        comment_count=len(t.comments) if t.comments else 0,
        attachment_count=len(t.attachments) if t.attachments else 0,
        tickets_count=t_count,
        open_tickets_count=open_t_count,
        ticket_count=t_count,
        assignee=assignee_actor,
        reporter=reporter_actor,
        assigner=reporter_actor,
        permissions=_compute_task_permissions(t, current_user),
    )


# ---------------------------------------------------------------------------
# Enum reference endpoint (public / meta for frontend dropdowns)
# ---------------------------------------------------------------------------

@router.get("/meta/enums", summary="Task enum values and display labels")
async def task_enums():
    return {
        "status": [{"value": e.value, "label": e.value.replace("_", " ").title()} for e in TaskStatus],
        "priority": [{"value": e.value, "label": e.value.capitalize()} for e in TaskPriority],
        "task_type": [{"value": e.value, "label": e.value.capitalize()} for e in TaskType],
    }


# ---------------------------------------------------------------------------
# Task List / Filter (Multi-Tenant Scoped)
# ---------------------------------------------------------------------------

@router.get("", response_model=TaskListResponse, summary="List tasks (paginated + filtered)")
@router.get("/", response_model=TaskListResponse, include_in_schema=False)
async def list_tasks(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    status: Optional[str] = Query(None),
    priority: Optional[str] = Query(None),
    task_type: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    assignee_id: Optional[str] = Query(None),
    assigned_to_id: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Return all workspace tasks matching criteria, strictly scoped to current user's organization.
    """
    target_assignee = assignee_id or assigned_to_id
    if target_assignee and target_assignee.lower() == "all":
        target_assignee = None

    base_filter = [Task.organization_id == current_user.organization_id]

    if project_id and project_id.lower() != "all":
        base_filter.append(Task.project_id == project_id)
    if status and status.lower() != "all":
        stat_val = status.lower()
        base_filter.append(
            func.lower(Task.status).in_([
                stat_val,
                stat_val.replace("to_do", "todo"),
                stat_val.replace("todo", "to_do"),
            ])
        )
    if priority and priority.lower() != "all":
        base_filter.append(func.lower(Task.priority) == priority.lower())
    if task_type and task_type.lower() != "all":
        base_filter.append(func.lower(Task.task_type) == task_type.lower())
    if target_assignee:
        base_filter.append(Task.assignee_id == target_assignee)
    if search:
        q = f"%{search.strip().lower()}%"
        base_filter.append(or_(func.lower(Task.title).like(q), func.lower(Task.task_number).like(q)))

    # Count total
    count_query = select(func.count(Task.id)).where(*base_filter)
    count_res = await db.execute(count_query)
    total = count_res.scalar() or 0

    # Paginate query
    query = (
        select(Task)
        .options(
            selectinload(Task.assignee),
            selectinload(Task.reporter),
            selectinload(Task.comments),
            selectinload(Task.attachments),
            selectinload(Task.tickets),
        )
        .where(*base_filter)
        .order_by(desc(Task.created_at))
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    res = await db.execute(query)
    tasks = res.scalars().all()

    items = [_format_task_read(t, current_user) for t in tasks]
    return TaskListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/my", response_model=TaskListResponse, summary="My tasks (assigned to current user)")
async def list_my_tasks(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    status: Optional[str] = Query(None),
    priority: Optional[str] = Query(None),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Return tasks assigned to current authenticated user within their organization."""
    resp = await list_tasks(
        page=page,
        page_size=page_size,
        status=status,
        priority=priority,
        task_type=None,
        project_id=None,
        assignee_id=current_user.id,
        assigned_to_id=None,
        search=None,
        current_user=current_user,
        db=db,
    )
    my_stats = await get_my_tasks_stats(
        db=db,
        user_id=current_user.id,
        organization_id=current_user.organization_id,
    )
    resp.stats = my_stats
    resp.todo_count = my_stats["todo"]
    resp.in_progress_count = my_stats["in_progress"]
    resp.done_count = my_stats["done"]
    if not status or status.lower() == "all":
        resp.total = my_stats["total"]
    return resp


# ---------------------------------------------------------------------------
# Single Task CRUD
# ---------------------------------------------------------------------------

@router.get("/{task_id}", response_model=TaskRead, summary="Get task by ID")
async def get_task(
    task_id: str,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Fetch single task by ID, scoped to user's organization."""
    res = await db.execute(
        select(Task)
        .options(
            selectinload(Task.assignee),
            selectinload(Task.reporter),
            selectinload(Task.comments),
            selectinload(Task.attachments),
            selectinload(Task.tickets),
        )
        .where(Task.id == task_id, Task.organization_id == current_user.organization_id)
    )
    t = res.scalars().first()
    if not t:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return _format_task_read(t, current_user)


@router.post("", response_model=TaskRead, status_code=status.HTTP_201_CREATED, summary="Create task")
@router.post("/", response_model=TaskRead, status_code=status.HTTP_201_CREATED, include_in_schema=False)
async def create_task(
    payload: TaskCreate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new task scoped to current tenant and broadcast to real-time subscribers."""
    assignee_id = payload.assignee_id or payload.assigned_to_id
    assigner_id = payload.assigner_id or payload.reporter_id or current_user.id
    task_id = str(uuid4())

    # RBAC: Changing assigner from self requires elevated privilege (owner, admin, manager, lead)
    user_role = (current_user.role or "").lower()
    can_change_assigner = user_role in ("owner", "admin", "manager", "lead")
    if assigner_id != current_user.id and not can_change_assigner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Only workspace leads, managers, or administrators can assign tasks on behalf of another user."
        )

    # Validate assigner belongs to organization
    if assigner_id != current_user.id:
        assigner_res = await db.execute(
            select(User)
            .outerjoin(Membership, Membership.user_id == User.id)
            .where(
                User.id == assigner_id,
                or_(
                    User.organization_id == current_user.organization_id,
                    and_(
                        Membership.organization_id == current_user.organization_id,
                        Membership.status == "active",
                    ),
                ),
            )
        )
        if not assigner_res.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Assigner does not belong to your organization"
            )

    # Validate project belongs to user's organization
    project = None
    if payload.project_id:
        p_res = await db.execute(
            select(Project).where(
                Project.id == payload.project_id,
                Project.organization_id == current_user.organization_id
            )
        )
        project = p_res.scalars().first()
        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Referenced project does not exist or does not belong to your organization"
            )

    # Validate assignee belongs to organization
    if assignee_id and assignee_id != current_user.id:
        u_res = await db.execute(
            select(User)
            .outerjoin(Membership, Membership.user_id == User.id)
            .where(
                User.id == assignee_id,
                or_(
                    User.organization_id == current_user.organization_id,
                    and_(
                        Membership.organization_id == current_user.organization_id,
                        Membership.status == "active",
                    ),
                ),
            )
        )
        if not u_res.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Assignee does not belong to your organization"
            )

    # Generate sequential project-specific task number or org-level task number
    prefix = project.key if project else "TASK"
    if project:
        count_res = await db.execute(select(func.count(Task.id)).where(Task.project_id == payload.project_id))
    else:
        count_res = await db.execute(select(func.count(Task.id)).where(Task.organization_id == current_user.organization_id))
    count = count_res.scalar() or 0

    candidate_num = 101 + count
    while True:
        candidate_str = f"{prefix}-{candidate_num}"
        existing = await db.execute(select(Task.id).where(Task.task_number == candidate_str))
        if not existing.scalar():
            task_number = candidate_str
            break
        candidate_num += 1

    now = datetime.now(timezone.utc)
    new_task = Task(
        id=task_id,
        task_number=task_number,
        title=payload.title.strip(),
        description=payload.description,
        status=payload.status or "todo",
        priority=payload.priority or "medium",
        task_type=payload.task_type or "feature",
        story_points=payload.story_points,
        due_date=payload.due_date,
        project_id=payload.project_id,
        assignee_id=assignee_id,
        reporter_id=assigner_id,
        organization_id=current_user.organization_id,
        created_at=now,
        updated_at=now,
    )
    db.add(new_task)
    await db.flush()

    task_activity = TaskActivity(
        id=str(uuid4()),
        task_id=new_task.id,
        organization_id=current_user.organization_id,
        actor_id=current_user.id,
        action="created",
        field_name=None,
        old_value=None,
        new_value=_sanitize_value(new_task.title),
        meta={"task_number": new_task.task_number, "title": new_task.title},
        created_at=now,
    )
    db.add(task_activity)

    if project:
        activity = ProjectActivity(
            project_id=project.id,
            actor_id=current_user.id,
            action="task_created",
            category="tasks",
            target_type="task",
            target_id=new_task.id,
            target_title=new_task.title,
            details=f"Created task {new_task.task_number}",
            created_at=now,
        )
        db.add(activity)

    # Link related tickets in the same transaction if provided
    target_ticket_ids = []
    p_ticket_ids = getattr(payload, "ticket_ids", None)
    p_ticket_id = getattr(payload, "ticket_id", None)
    if p_ticket_ids:
        target_ticket_ids.extend([tid for tid in p_ticket_ids if tid])
    elif p_ticket_id:
        target_ticket_ids.append(p_ticket_id)

    target_ticket_ids = list(dict.fromkeys(target_ticket_ids))
    if target_ticket_ids:
        t_tickets_res = await db.execute(
            select(Ticket)
            .options(selectinload(Ticket.project))
            .where(Ticket.id.in_(target_ticket_ids))
        )
        t_tickets = t_tickets_res.scalars().all()
        found_ids = {t.id for t in t_tickets}
        missing = set(target_ticket_ids) - found_ids
        if missing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"One or more referenced tickets not found: {list(missing)}"
            )

        for tick in t_tickets:
            if not tick.project or tick.project.organization_id != current_user.organization_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Ticket {tick.ticket_number or tick.id} does not belong to your organization"
                )
            if new_task.project_id and tick.project_id and tick.project_id != new_task.project_id:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Ticket {tick.ticket_number or tick.id} belongs to a different project"
                )
            if not new_task.project_id and tick.project_id:
                new_task.project_id = tick.project_id

            tick.task_id = new_task.id

            act_link = TaskActivity(
                id=str(uuid4()),
                task_id=new_task.id,
                organization_id=current_user.organization_id,
                actor_id=current_user.id,
                action="ticket_linked",
                field_name="task_id",
                old_value=None,
                new_value=tick.ticket_number,
                meta={"ticket_id": tick.id, "ticket_number": tick.ticket_number, "ticket_title": tick.title},
                created_at=now,
            )
            db.add(act_link)

            tick_act = TicketActivity(
                ticket_id=tick.id,
                actor_id=current_user.id,
                action="task_linked",
                old_value=None,
                new_value=new_task.task_number,
                created_at=now,
            )
            db.add(tick_act)

    await db.commit()

    # Re-fetch with relationships loaded
    res = await db.execute(
        select(Task)
        .options(
            selectinload(Task.assignee),
            selectinload(Task.reporter),
            selectinload(Task.comments),
            selectinload(Task.attachments),
            selectinload(Task.tickets),
        )
        .where(Task.id == task_id)
    )
    saved_task = res.scalars().first()
    task_read = _format_task_read(saved_task, current_user)

    # Real-time broadcast to organization workspace
    await ws_manager.broadcast_to_workspace(
        current_user.organization_id,
        {"event": "task_created", "task": task_read.model_dump(mode="json")}
    )
    act_read = _format_task_activity_read(task_activity, current_user)
    await ws_manager.broadcast_to_workspace(
        current_user.organization_id,
        {"event": "task_activity", "activity": act_read.model_dump(mode="json")}
    )

    return task_read


@router.patch("/{task_id}", response_model=TaskRead, summary="Update task")
@router.put("/{task_id}", response_model=TaskRead, summary="Update task (PUT)")
async def update_task(
    task_id: str,
    payload: TaskUpdate,
    request: Request,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Update task fields with strict server-side RBAC and field-level permission checks."""
    raw_keys = set()
    try:
        raw_body = await request.json()
        if isinstance(raw_body, dict):
            raw_keys = set(raw_body.keys())
    except Exception:
        raw_keys = set()

    provided_fields = raw_keys if raw_keys else payload.model_fields_set

    # Check for unknown or forbidden fields (e.g. organization_id or arbitrary keys)
    unknown_fields = provided_fields - ALLOWED_TASK_UPDATE_FIELDS
    if unknown_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "task_edit_forbidden",
                "message": f"Unknown or forbidden field(s): {', '.join(sorted(unknown_fields))}"
            }
        )

    res = await db.execute(
        select(Task)
        .options(
            selectinload(Task.assignee),
            selectinload(Task.reporter),
            selectinload(Task.comments),
            selectinload(Task.attachments),
            selectinload(Task.tickets),
        )
        .where(Task.id == task_id, Task.organization_id == current_user.organization_id)
    )
    t = res.scalars().first()
    if not t:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    user_role = (current_user.role or "").lower().strip()
    is_admin_or_owner = user_role in ("owner", "admin")
    is_creator = (current_user.id == t.reporter_id)
    is_assignee = (current_user.id == t.assignee_id)

    can_edit_details = is_creator or is_admin_or_owner
    can_change_status = is_creator or is_admin_or_owner or is_assignee

    # Field-level permission enforcement
    has_detail_changes = bool(provided_fields & TASK_DETAIL_FIELDS)
    has_status_changes = bool(provided_fields & TASK_STATUS_FIELDS)

    if has_detail_changes and not can_edit_details:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "task_edit_forbidden",
                "message": "Only the task creator, organization owner, or administrators can edit task details."
            }
        )

    if has_status_changes and not can_change_status:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "task_edit_forbidden",
                "message": "Only the task assignee, creator, organization owner, or administrators can change task status."
            }
        )

    now = datetime.now(timezone.utc)

    old_title = t.title
    old_description = t.description
    old_status = t.status
    old_priority = t.priority
    old_task_type = t.task_type
    old_story_points = t.story_points
    old_due_date = t.due_date
    old_project_id = t.project_id
    old_assignee_id = t.assignee_id
    old_reporter_id = t.reporter_id

    activities_to_broadcast: List[TaskActivity] = []

    def _log_field_activity(
        action: str,
        field_name: Optional[str],
        old_val: Optional[str],
        new_val: Optional[str],
        meta: Optional[dict] = None,
    ):
        act = TaskActivity(
            id=str(uuid4()),
            task_id=t.id,
            organization_id=current_user.organization_id,
            actor_id=current_user.id,
            action=action,
            field_name=field_name,
            old_value=_sanitize_value(old_val, field_name),
            new_value=_sanitize_value(new_val, field_name),
            meta=meta,
            created_at=now,
        )
        db.add(act)
        activities_to_broadcast.append(act)

    if payload.title is not None and "title" in provided_fields:
        new_title = payload.title.strip()
        if new_title != old_title:
            t.title = new_title
            _log_field_activity("field_updated", "title", old_title, new_title)

    if payload.description is not None and "description" in provided_fields:
        if payload.description != old_description:
            t.description = payload.description
            _log_field_activity("field_updated", "description", old_description, payload.description)

    if payload.status is not None and "status" in provided_fields:
        new_status = payload.status.lower().replace("to_do", "todo")
        if new_status != old_status:
            t.status = new_status
            _log_field_activity("status_changed", "status", old_status, new_status)
            if t.project_id:
                activity = ProjectActivity(
                    project_id=t.project_id,
                    actor_id=current_user.id,
                    action="task_status_changed",
                    category="tasks",
                    target_type="task",
                    target_id=t.id,
                    target_title=t.title,
                    details=f"Status changed from {old_status} to {t.status}",
                    created_at=now,
                )
                db.add(activity)

    if payload.priority is not None and "priority" in provided_fields:
        if payload.priority != old_priority:
            t.priority = payload.priority
            _log_field_activity("field_updated", "priority", old_priority, payload.priority)

    if (payload.task_type is not None or payload.type is not None) and bool(provided_fields & {"task_type", "type"}):
        new_type = payload.task_type or payload.type
        if new_type != old_task_type:
            t.task_type = new_type
            _log_field_activity("field_updated", "task_type", old_task_type, new_type)

    if payload.story_points is not None and "story_points" in provided_fields:
        if payload.story_points != old_story_points:
            t.story_points = payload.story_points
            _log_field_activity("field_updated", "story_points", str(old_story_points) if old_story_points is not None else None, str(payload.story_points))

    if payload.due_date is not None and "due_date" in provided_fields:
        if payload.due_date != old_due_date:
            t.due_date = payload.due_date
            _log_field_activity("field_updated", "due_date", old_due_date.isoformat() if old_due_date else None, payload.due_date.isoformat() if payload.due_date else None)

    if payload.project_id is not None and "project_id" in provided_fields:
        p_res = await db.execute(
            select(Project).where(
                Project.id == payload.project_id,
                Project.organization_id == current_user.organization_id
            )
        )
        if not p_res.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Referenced project does not exist or does not belong to your organization"
            )
        if payload.project_id != old_project_id:
            t.project_id = payload.project_id
            _log_field_activity("field_updated", "project_id", old_project_id, payload.project_id)

    target_assigner = payload.assigner_id or payload.reporter_id
    if target_assigner is not None and bool(provided_fields & {"assigner_id", "reporter_id"}):
        can_change_assigner = is_admin_or_owner or user_role in ("manager", "lead")
        if target_assigner != current_user.id and not can_change_assigner:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden: Only workspace leads, managers, or administrators can assign tasks on behalf of another user."
            )
        if target_assigner != current_user.id:
            assigner_res = await db.execute(
                select(User)
                .outerjoin(Membership, Membership.user_id == User.id)
                .where(
                    User.id == target_assigner,
                    or_(
                        User.organization_id == current_user.organization_id,
                        and_(
                            Membership.organization_id == current_user.organization_id,
                            Membership.status == "active",
                        ),
                    ),
                )
            )
            if not assigner_res.scalars().first():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Assigner does not belong to your organization"
                )
        if target_assigner != old_reporter_id:
            t.reporter_id = target_assigner
            _log_field_activity("field_updated", "reporter_id", old_reporter_id, target_assigner)

    target_assignee = payload.assignee_id or payload.assigned_to_id
    if target_assignee is not None and bool(provided_fields & {"assignee_id", "assigned_to_id"}):
        new_assignee_user = None
        if target_assignee != current_user.id:
            u_res = await db.execute(
                select(User)
                .outerjoin(Membership, Membership.user_id == User.id)
                .where(
                    User.id == target_assignee,
                    or_(
                        User.organization_id == current_user.organization_id,
                        and_(
                            Membership.organization_id == current_user.organization_id,
                            Membership.status == "active",
                        ),
                    ),
                )
            )
            new_assignee_user = u_res.scalars().first()
            if not new_assignee_user:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Assignee does not belong to your organization"
                )
        else:
            new_assignee_user = current_user

        if target_assignee != old_assignee_id:
            t.assignee_id = target_assignee
            old_name = _get_actor_display_name(t.assignee) if t.assignee else "Unassigned"
            new_name = _get_actor_display_name(new_assignee_user) if new_assignee_user else "Unassigned"
            _log_field_activity(
                "assignee_changed",
                "assignee_id",
                old_assignee_id,
                target_assignee,
                meta={"old_assignee_name": old_name, "new_assignee_name": new_name}
            )

    t.updated_at = now
    await db.commit()

    # Re-fetch with relationships loaded
    refetch = await db.execute(
        select(Task)
        .options(
            selectinload(Task.assignee),
            selectinload(Task.reporter),
            selectinload(Task.comments),
            selectinload(Task.attachments),
            selectinload(Task.tickets),
        )
        .where(Task.id == task_id)
    )
    updated_task = refetch.scalars().first()
    task_read = _format_task_read(updated_task, current_user)

    await ws_manager.broadcast_to_workspace(
        current_user.organization_id,
        {"event": "task_updated", "task": task_read.model_dump(mode="json")}
    )
    for act in activities_to_broadcast:
        act_read = _format_task_activity_read(act, current_user)
        await ws_manager.broadcast_to_workspace(
            current_user.organization_id,
            {"event": "task_activity", "activity": act_read.model_dump(mode="json")}
        )
    return task_read


@router.patch("/{task_id}/status", response_model=TaskRead, summary="Change task status")
@router.put("/{task_id}/status", response_model=TaskRead, summary="Change task status (PUT)")
async def change_task_status(
    task_id: str,
    payload: TaskStatusChange,
    request: Request,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Change status of a task and broadcast event."""
    try:
        raw_body = await request.json()
        if isinstance(raw_body, dict):
            extra_keys = set(raw_body.keys()) - {"status"}
            if extra_keys:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "task_edit_forbidden",
                        "message": f"Only status field is permitted on this endpoint. Disallowed: {', '.join(sorted(extra_keys))}"
                    }
                )
    except HTTPException:
        raise
    except Exception:
        pass

    res = await db.execute(
        select(Task).where(Task.id == task_id, Task.organization_id == current_user.organization_id)
    )
    t = res.scalars().first()
    if not t:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    user_role = (current_user.role or "").lower().strip()
    is_admin_or_owner = user_role in ("owner", "admin")
    is_creator = (current_user.id == t.reporter_id)
    is_assignee = (current_user.id == t.assignee_id)

    if not (is_creator or is_admin_or_owner or is_assignee):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "task_edit_forbidden",
                "message": "Only the task assignee, creator, organization owner, or administrators can change task status."
            }
        )

    res = await update_task(task_id, TaskUpdate(status=payload.status), request=request, current_user=current_user, db=db)
    await ws_manager.broadcast_to_workspace(
        current_user.organization_id,
        {"event": "task_status_changed", "task_id": task_id, "status": payload.status}
    )
    return res


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete task")
async def delete_task(
    task_id: str,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete task and broadcast deletion event (restricted to creator, owner, or admin)."""
    res = await db.execute(
        select(Task).where(Task.id == task_id, Task.organization_id == current_user.organization_id)
    )
    t = res.scalars().first()
    if not t:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    user_role = (current_user.role or "").lower().strip()
    is_admin_or_owner = user_role in ("owner", "admin")
    is_creator = (current_user.id == t.reporter_id)

    if not (is_creator or is_admin_or_owner):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "task_edit_forbidden",
                "message": "Only the task creator, organization owner, or administrators can delete tasks."
            }
        )

    # Deleting a task keeps its tickets (task_id becomes null) and logs it
    now_utc = datetime.now(timezone.utc)
    t_ticks = await db.execute(select(Ticket).where(Ticket.task_id == t.id))
    linked_tickets = t_ticks.scalars().all()
    for tk in linked_tickets:
        tk.task_id = None
        act = TicketActivity(
            ticket_id=tk.id,
            actor_id=current_user.id,
            action="task_unlinked",
            old_value=t.task_number,
            new_value=None,
            created_at=now_utc,
        )
        db.add(act)

    if t.project_id:
        activity = ProjectActivity(
            project_id=t.project_id,
            actor_id=current_user.id,
            action="task_deleted",
            category="tasks",
            target_type="task",
            target_id=t.id,
            target_title=t.title,
            details=f"Deleted task {t.task_number}",
            created_at=datetime.now(timezone.utc),
        )
        db.add(activity)
    else:
        audit = AuditLog(
            organization_id=t.organization_id,
            actor_id=current_user.id,
            action="task_deleted",
            details=f"Deleted task {t.task_number}: {t.title}",
            created_at=datetime.now(timezone.utc),
        )
        db.add(audit)

    await db.delete(t)
    await db.commit()

    await ws_manager.broadcast_to_workspace(
        current_user.organization_id,
        {"event": "task_deleted", "task_id": task_id}
    )


# ---------------------------------------------------------------------------
# Comments (Scoped to Organization)
# ---------------------------------------------------------------------------

def _build_comment_read(c: TaskComment) -> TaskCommentRead:
    author = getattr(c, "author", None)
    author_name = "Deleted user"
    author_avatar = None
    author_role = None
    author_actor = None
    if author:
        author_name = (author.display_name or author.full_name or "").strip() or "Deleted user"
        author_avatar = author.avatar_url
        author_role = author.role
        author_actor = ActorSchema(
            id=author.id,
            full_name=author_name,
            display_name=author.display_name or author_name,
            avatar_url=author.avatar_url,
            role=author.role,
        )
    elif c.author_id:
        author_name = "Deleted user"
        author_actor = ActorSchema(
            id=c.author_id,
            full_name="Deleted user",
            display_name="Deleted user",
            avatar_url=None,
            role=None,
        )
    return TaskCommentRead(
        id=c.id,
        body=c.body,
        task_id=c.task_id,
        author_id=c.author_id,
        author_name=author_name,
        author_avatar=author_avatar,
        author_role=author_role,
        author=author_actor,
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


@router.get("/{task_id}/comments", response_model=List[TaskCommentRead], summary="List task comments")
async def list_comments(
    task_id: str,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """List comments for a task in user's organization."""
    t_res = await db.execute(
        select(Task).where(Task.id == task_id, Task.organization_id == current_user.organization_id)
    )
    if not t_res.scalars().first():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    res = await db.execute(
        select(TaskComment)
        .options(selectinload(TaskComment.author))
        .where(TaskComment.task_id == task_id)
        .order_by(TaskComment.created_at.asc())
    )
    comments = res.scalars().all()
    return [_build_comment_read(c) for c in comments]


@router.post("/{task_id}/comments", response_model=TaskCommentRead, status_code=status.HTTP_201_CREATED, summary="Add comment")
async def add_comment(
    task_id: str,
    payload: TaskCommentCreate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Add a new comment to a task."""
    t_res = await db.execute(
        select(Task).where(Task.id == task_id, Task.organization_id == current_user.organization_id)
    )
    if not t_res.scalars().first():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    cid = str(uuid4())
    now = datetime.now(timezone.utc)
    c_obj = TaskComment(
        id=cid,
        task_id=task_id,
        author_id=current_user.id,
        body=payload.body,
        created_at=now,
    )
    db.add(c_obj)

    # Activity recorded in the same transaction
    act = TaskActivity(
        id=str(uuid4()),
        task_id=task_id,
        organization_id=current_user.organization_id,
        actor_id=current_user.id,
        action="comment_added",
        field_name=None,
        old_value=None,
        new_value=_sanitize_value(payload.body),
        meta={"comment_id": cid, "snippet": _sanitize_value(payload.body[:100])},
        created_at=now,
    )
    db.add(act)
    await db.commit()

    c_res = await db.execute(
        select(TaskComment).options(selectinload(TaskComment.author)).where(TaskComment.id == cid)
    )
    saved_comment = c_res.scalars().first()
    comment_read = _build_comment_read(saved_comment)

    await ws_manager.broadcast_to_workspace(
        current_user.organization_id,
        {"event": "comment_added", "task_id": task_id, "comment": comment_read.model_dump(mode="json")}
    )
    act_read = _format_task_activity_read(act, current_user)
    await ws_manager.broadcast_to_workspace(
        current_user.organization_id,
        {"event": "task_activity", "activity": act_read.model_dump(mode="json")}
    )
    return comment_read


@router.patch("/{task_id}/comments/{comment_id}", response_model=TaskCommentRead, summary="Edit comment")
@router.put("/{task_id}/comments/{comment_id}", response_model=TaskCommentRead, summary="Edit comment (PUT)")
async def update_comment(
    task_id: str,
    comment_id: str,
    payload: TaskCommentUpdate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Edit comment body."""
    t_res = await db.execute(
        select(Task).where(Task.id == task_id, Task.organization_id == current_user.organization_id)
    )
    task = t_res.scalars().first()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    c_res = await db.execute(
        select(TaskComment).options(selectinload(TaskComment.author)).where(TaskComment.id == comment_id, TaskComment.task_id == task_id)
    )
    comment = c_res.scalars().first()
    if not comment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comment not found")

    user_role = (current_user.role or "").lower().strip()
    is_admin_or_owner = user_role in ("owner", "admin")
    is_author = (current_user.id == comment.author_id)
    if not (is_author or is_admin_or_owner):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot edit another user's comment")

    old_body = comment.body
    now = datetime.now(timezone.utc)
    comment.body = payload.body
    comment.updated_at = now

    act = TaskActivity(
        id=str(uuid4()),
        task_id=task_id,
        organization_id=current_user.organization_id,
        actor_id=current_user.id,
        action="comment_edited",
        field_name=None,
        old_value=_sanitize_value(old_body),
        new_value=_sanitize_value(payload.body),
        meta={"comment_id": comment_id, "snippet": _sanitize_value(payload.body[:100])},
        created_at=now,
    )
    db.add(act)
    await db.commit()

    comment_read = _build_comment_read(comment)
    await ws_manager.broadcast_to_workspace(
        current_user.organization_id,
        {"event": "comment_updated", "task_id": task_id, "comment": comment_read.model_dump(mode="json")}
    )
    act_read = _format_task_activity_read(act, current_user)
    await ws_manager.broadcast_to_workspace(
        current_user.organization_id,
        {"event": "task_activity", "activity": act_read.model_dump(mode="json")}
    )
    return comment_read


@router.delete("/{task_id}/comments/{comment_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete comment")
async def delete_comment(
    task_id: str,
    comment_id: str,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a task comment."""
    t_res = await db.execute(
        select(Task).where(Task.id == task_id, Task.organization_id == current_user.organization_id)
    )
    task = t_res.scalars().first()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    c_res = await db.execute(
        select(TaskComment).where(TaskComment.id == comment_id, TaskComment.task_id == task_id)
    )
    comment = c_res.scalars().first()
    if not comment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comment not found")

    user_role = (current_user.role or "").lower().strip()
    is_admin_or_owner = user_role in ("owner", "admin")
    is_author = (current_user.id == comment.author_id)
    if not (is_author or is_admin_or_owner):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot delete another user's comment")

    now = datetime.now(timezone.utc)
    act = TaskActivity(
        id=str(uuid4()),
        task_id=task_id,
        organization_id=current_user.organization_id,
        actor_id=current_user.id,
        action="comment_deleted",
        field_name=None,
        old_value=_sanitize_value(comment.body),
        new_value=None,
        meta={"comment_id": comment_id},
        created_at=now,
    )
    db.add(act)
    await db.delete(comment)
    await db.commit()

    await ws_manager.broadcast_to_workspace(
        current_user.organization_id,
        {"event": "comment_deleted", "task_id": task_id, "comment_id": comment_id}
    )
    act_read = _format_task_activity_read(act, current_user)
    await ws_manager.broadcast_to_workspace(
        current_user.organization_id,
        {"event": "task_activity", "activity": act_read.model_dump(mode="json")}
    )


# ---------------------------------------------------------------------------
# Attachments (Scoped to Organization)
# ---------------------------------------------------------------------------

@router.get("/{task_id}/attachments", response_model=List[TaskAttachmentRead], summary="List task attachments")
async def list_attachments(
    task_id: str,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """List file attachment metadata for a task."""
    t_res = await db.execute(
        select(Task).where(Task.id == task_id, Task.organization_id == current_user.organization_id)
    )
    if not t_res.scalars().first():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    res = await db.execute(
        select(TaskAttachment).where(TaskAttachment.task_id == task_id).order_by(TaskAttachment.created_at.asc())
    )
    attachments = res.scalars().all()
    return [
        TaskAttachmentRead(
            id=a.id,
            filename=a.filename,
            file_size=a.file_size,
            mime_type=a.mime_type,
            storage_key=a.storage_key,
            uploader_id=a.uploader_id,
            task_id=a.task_id,
            created_at=a.created_at,
        )
        for a in attachments
    ]


@router.post("/{task_id}/attachments", response_model=TaskAttachmentRead, status_code=status.HTTP_201_CREATED, summary="Register attachment metadata")
async def register_attachment(
    task_id: str,
    payload: TaskAttachmentCreate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Register attachment metadata after upload."""
    t_res = await db.execute(
        select(Task).where(Task.id == task_id, Task.organization_id == current_user.organization_id)
    )
    if not t_res.scalars().first():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    aid = str(uuid4())
    now = datetime.now(timezone.utc)
    a_obj = TaskAttachment(
        id=aid,
        filename=payload.filename,
        file_size=payload.file_size,
        mime_type=payload.mime_type,
        storage_key=payload.storage_key,
        task_id=task_id,
        uploader_id=current_user.id,
        created_at=now,
    )
    db.add(a_obj)

    # Activity recorded in the same transaction
    act = TaskActivity(
        id=str(uuid4()),
        task_id=task_id,
        organization_id=current_user.organization_id,
        actor_id=current_user.id,
        action="attachment_added",
        field_name=None,
        old_value=None,
        new_value=_sanitize_value(payload.filename),
        meta={"attachment_id": aid, "filename": payload.filename, "file_size": payload.file_size},
        created_at=now,
    )
    db.add(act)
    await db.commit()

    att_read = TaskAttachmentRead(
        id=aid,
        filename=payload.filename,
        file_size=payload.file_size,
        mime_type=payload.mime_type,
        storage_key=payload.storage_key,
        task_id=task_id,
        uploader_id=current_user.id,
        created_at=now,
    )
    act_read = _format_task_activity_read(act, current_user)
    await ws_manager.broadcast_to_workspace(
        current_user.organization_id,
        {"event": "task_activity", "activity": act_read.model_dump(mode="json")}
    )
    return att_read


@router.delete("/{task_id}/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete attachment")
async def delete_attachment(
    task_id: str,
    attachment_id: str,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a task attachment."""
    t_res = await db.execute(
        select(Task).where(Task.id == task_id, Task.organization_id == current_user.organization_id)
    )
    task = t_res.scalars().first()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    a_res = await db.execute(
        select(TaskAttachment).where(TaskAttachment.id == attachment_id, TaskAttachment.task_id == task_id)
    )
    attachment = a_res.scalars().first()
    if not attachment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found")

    user_role = (current_user.role or "").lower().strip()
    is_admin_or_owner = user_role in ("owner", "admin")
    is_uploader = (current_user.id == attachment.uploader_id)
    is_creator = (current_user.id == task.reporter_id)
    if not (is_uploader or is_creator or is_admin_or_owner):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot delete this attachment")

    now = datetime.now(timezone.utc)
    act = TaskActivity(
        id=str(uuid4()),
        task_id=task_id,
        organization_id=current_user.organization_id,
        actor_id=current_user.id,
        action="attachment_removed",
        field_name=None,
        old_value=_sanitize_value(attachment.filename),
        new_value=None,
        meta={"attachment_id": attachment_id, "filename": attachment.filename},
        created_at=now,
    )
    db.add(act)
    await db.delete(attachment)
    await db.commit()

    act_read = _format_task_activity_read(act, current_user)
    await ws_manager.broadcast_to_workspace(
        current_user.organization_id,
        {"event": "task_activity", "activity": act_read.model_dump(mode="json")}
    )


# ---------------------------------------------------------------------------
# Ticket Linking (Task 1 & Task 3)
# ---------------------------------------------------------------------------

@router.post("/{task_id}/tickets", summary="Link ticket to task")
@router.post("/{task_id}/tickets/{ticket_id}", summary="Link ticket to task by path ID")
async def link_ticket(
    task_id: str,
    ticket_id: Optional[str] = None,
    payload: Optional[LinkTicketRequest] = None,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Link an organization ticket to this task."""
    target_ticket_id = ticket_id or (payload.ticket_id if payload else None)
    if not target_ticket_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ticket_id is required")

    t_res = await db.execute(
        select(Task).where(Task.id == task_id, Task.organization_id == current_user.organization_id)
    )
    task = t_res.scalars().first()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    tick_res = await db.execute(
        select(Ticket)
        .options(selectinload(Ticket.project))
        .where(Ticket.id == target_ticket_id)
    )
    ticket = tick_res.scalars().first()
    if not ticket or (ticket.project and ticket.project.organization_id != current_user.organization_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")

    # If task has a project, ticket must belong to the same project
    if task.project_id and ticket.project_id and ticket.project_id != task.project_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Ticket must belong to the same project as the task"
        )
    if not task.project_id and ticket.project_id:
        task.project_id = ticket.project_id

    # If already linked to this task, return early
    if ticket.task_id == task.id:
        return {"status": "ok", "message": "Ticket already linked to this task", "task_id": task.id, "ticket_id": ticket.id}

    ticket.task_id = task.id
    now = datetime.now(timezone.utc)
    act = TaskActivity(
        id=str(uuid4()),
        task_id=task.id,
        organization_id=current_user.organization_id,
        actor_id=current_user.id,
        action="ticket_linked",
        field_name="task_id",
        old_value=None,
        new_value=ticket.ticket_number,
        meta={"ticket_id": ticket.id, "ticket_number": ticket.ticket_number, "ticket_title": ticket.title},
        created_at=now,
    )
    db.add(act)

    # Record TicketActivity on the linked ticket
    t_act = TicketActivity(
        ticket_id=ticket.id,
        actor_id=current_user.id,
        action="task_linked",
        old_value=None,
        new_value=task.task_number,
        created_at=now,
    )
    db.add(t_act)
    await db.commit()

    act_read = _format_task_activity_read(act, current_user)
    await ws_manager.broadcast_to_workspace(
        current_user.organization_id,
        {"event": "task_activity", "activity": act_read.model_dump(mode="json")}
    )
    return {"status": "linked", "task_id": task.id, "ticket_id": ticket.id, "ticket_number": ticket.ticket_number}


@router.get("/{task_id}/tickets", response_model=TaskTicketListResponse, summary="Get tickets linked to task")
async def get_task_tickets(
    task_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=50),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get paginated tickets linked to this task, organization scoped.
    """
    t_res = await db.execute(
        select(Task).where(Task.id == task_id, Task.organization_id == current_user.organization_id)
    )
    task = t_res.scalars().first()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    count_res = await db.execute(
        select(func.count(Ticket.id))
        .join(Ticket.project)
        .where(
            Ticket.task_id == task_id,
            Project.organization_id == current_user.organization_id,
        )
    )
    total = count_res.scalar() or 0

    stmt = (
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
        .where(
            Ticket.task_id == task_id,
            Project.organization_id == current_user.organization_id,
        )
        .order_by(desc(Ticket.created_at))
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    res = await db.execute(stmt)
    tickets = res.scalars().all()

    items = [TicketRead.model_validate(tk) for tk in tickets]
    return TaskTicketListResponse(items=items, total=total, page=page, page_size=page_size)


@router.delete("/{task_id}/tickets/{ticket_id}", summary="Unlink ticket from task")
async def unlink_ticket(
    task_id: str,
    ticket_id: str,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Unlink a ticket from this task."""
    t_res = await db.execute(
        select(Task).where(Task.id == task_id, Task.organization_id == current_user.organization_id)
    )
    task = t_res.scalars().first()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    tick_res = await db.execute(
        select(Ticket)
        .options(selectinload(Ticket.project))
        .where(Ticket.id == ticket_id)
    )
    ticket = tick_res.scalars().first()
    if not ticket or ticket.task_id != task.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Linked ticket not found")

    ticket.task_id = None
    now = datetime.now(timezone.utc)
    act = TaskActivity(
        id=str(uuid4()),
        task_id=task.id,
        organization_id=current_user.organization_id,
        actor_id=current_user.id,
        action="ticket_unlinked",
        field_name="task_id",
        old_value=ticket.ticket_number,
        new_value=None,
        meta={"ticket_id": ticket.id, "ticket_number": ticket.ticket_number, "ticket_title": ticket.title},
        created_at=now,
    )
    db.add(act)

    t_act = TicketActivity(
        ticket_id=ticket.id,
        actor_id=current_user.id,
        action="task_unlinked",
        old_value=task.task_number,
        new_value=None,
        created_at=now,
    )
    db.add(t_act)
    await db.commit()

    act_read = _format_task_activity_read(act, current_user)
    await ws_manager.broadcast_to_workspace(
        current_user.organization_id,
        {"event": "task_activity", "activity": act_read.model_dump(mode="json")}
    )
    return {"status": "unlinked", "task_id": task.id, "ticket_id": ticket.id}


# ---------------------------------------------------------------------------
# Task Activity Log (Task 3)
# ---------------------------------------------------------------------------

@router.get("/{task_id}/activity", response_model=TaskActivityListResponse, summary="Get task activity log")
async def get_task_activity(
    task_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=50),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get paginated task activity log, newest first, max page size 50.
    Scoped to current user's organization and only visible to authorized viewers.
    """
    t_res = await db.execute(
        select(Task).where(Task.id == task_id, Task.organization_id == current_user.organization_id)
    )
    task = t_res.scalars().first()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    count_res = await db.execute(
        select(func.count(TaskActivity.id)).where(
            TaskActivity.task_id == task_id,
            TaskActivity.organization_id == current_user.organization_id,
        )
    )
    total = count_res.scalar() or 0

    stmt = (
        select(TaskActivity)
        .options(selectinload(TaskActivity.actor))
        .where(
            TaskActivity.task_id == task_id,
            TaskActivity.organization_id == current_user.organization_id,
        )
        .order_by(desc(TaskActivity.created_at))
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    res = await db.execute(stmt)
    activities = res.scalars().all()

    items = [_format_task_activity_read(act, act.actor) for act in activities]
    return TaskActivityListResponse(items=items, total=total, page=page, page_size=page_size)


# ---------------------------------------------------------------------------
# WebSocket Endpoint (Authenticated and Scoped)
# ---------------------------------------------------------------------------

@router.websocket("/ws")
@router.websocket("/ws/{workspace_id}")
async def tasks_ws(
    websocket: WebSocket,
    workspace_id: Optional[str] = None,
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """
    Authenticated real-time task event stream scoped to user's organization workspace.
    """
    user = await authenticate_ws(websocket, token, db, required_workspace_id=workspace_id)
    if not user:
        return

    target_ws = workspace_id or user.organization_id
    await ws_manager.connect(websocket, target_ws)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, target_ws)
    except Exception:
        ws_manager.disconnect(websocket, target_ws)
