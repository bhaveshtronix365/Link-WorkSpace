"""
Task schemas — Pydantic v2 models for request/response validation.
Enum values are lowercase strings matching backend/app/enums.py and
frontend/src/modules/tasks/constants/taskEnums.js.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Dict
from pydantic import BaseModel, ConfigDict, model_validator
from backend.app.schemas import TicketRead


class BaseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Actor (minimal user representation for responses)
# ---------------------------------------------------------------------------

class ActorSchema(BaseSchema):
    id: str
    full_name: str
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None
    role: Optional[str] = None


# ---------------------------------------------------------------------------
# Task Attachment
# ---------------------------------------------------------------------------

class TaskAttachmentCreate(BaseSchema):
    filename: str
    file_size: int
    mime_type: str
    storage_key: str


class TaskAttachmentRead(BaseSchema):
    id: str
    filename: str
    file_size: int
    mime_type: str
    storage_key: str
    uploader_id: str
    task_id: str
    created_at: datetime


# ---------------------------------------------------------------------------
# Task Comment
# ---------------------------------------------------------------------------

class TaskCommentCreate(BaseSchema):
    body: str


class TaskCommentUpdate(BaseSchema):
    body: str


class TaskCommentRead(BaseSchema):
    id: str
    body: str
    task_id: str
    author_id: str
    author_name: str = "Deleted user"
    author_avatar: Optional[str] = None
    author_role: Optional[str] = None
    author: Optional[ActorSchema] = None
    created_at: datetime
    updated_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Task Activity & Ticket Link Schemas
# ---------------------------------------------------------------------------

class TaskActivityRead(BaseSchema):
    id: str
    task_id: str
    organization_id: str
    actor_id: Optional[str] = None
    actor_name: str
    actor_avatar: Optional[str] = None
    action: str
    field_name: Optional[str] = None
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    summary: str
    meta: Optional[dict] = None
    created_at: datetime


class TaskActivityListResponse(BaseSchema):
    items: List[TaskActivityRead]
    total: int
    page: int
    page_size: int


class LinkTicketRequest(BaseSchema):
    ticket_id: str


class TaskPermissions(BaseSchema):
    can_edit_details: bool
    can_change_status: bool
    can_delete: bool


# ---------------------------------------------------------------------------
# Task
# ---------------------------------------------------------------------------

class TaskCreate(BaseSchema):
    title: str
    description: Optional[str] = None
    status: str = "todo"            # TaskStatus enum value
    priority: str = "medium"        # TaskPriority enum value
    task_type: str = "feature"      # TaskType enum value
    type: Optional[str] = None
    story_points: Optional[int] = None
    due_date: Optional[datetime] = None
    project_id: Optional[str] = None
    assignee_id: Optional[str] = None
    assigned_to_id: Optional[str] = None
    reporter_id: Optional[str] = None
    assigner_id: Optional[str] = None
    ticket_id: Optional[str] = None
    ticket_ids: Optional[List[str]] = None

    @model_validator(mode="after")
    def sync_assignee(self):
        if not self.task_type and self.type:
            self.task_type = self.type
        elif not self.type and self.task_type:
            self.type = self.task_type
        if not self.assignee_id and self.assigned_to_id:
            self.assignee_id = self.assigned_to_id
        elif not self.assigned_to_id and self.assignee_id:
            self.assigned_to_id = self.assignee_id
        if not self.reporter_id and self.assigner_id:
            self.reporter_id = self.assigner_id
        elif not self.assigner_id and self.reporter_id:
            self.assigner_id = self.reporter_id
        if self.status:
            self.status = self.status.lower().replace("to_do", "todo")
        return self


class TaskUpdate(BaseSchema):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    task_type: Optional[str] = None
    type: Optional[str] = None
    story_points: Optional[int] = None
    due_date: Optional[datetime] = None
    project_id: Optional[str] = None
    assignee_id: Optional[str] = None
    assigned_to_id: Optional[str] = None
    reporter_id: Optional[str] = None
    assigner_id: Optional[str] = None
    ticket_id: Optional[str] = None
    ticket_ids: Optional[List[str]] = None

    @model_validator(mode="after")
    def sync_assignee(self):
        if not self.task_type and self.type:
            self.task_type = self.type
        elif not self.type and self.task_type:
            self.type = self.task_type
        if not self.assignee_id and self.assigned_to_id:
            self.assignee_id = self.assigned_to_id
        elif not self.assigned_to_id and self.assignee_id:
            self.assigned_to_id = self.assignee_id
        if not self.reporter_id and self.assigner_id:
            self.reporter_id = self.assigner_id
        elif not self.assigner_id and self.reporter_id:
            self.assigner_id = self.reporter_id
        if self.status:
            self.status = self.status.lower().replace("to_do", "todo")
        return self


class TaskStatusChange(BaseSchema):
    status: str    # must be a valid TaskStatus value

    @model_validator(mode="after")
    def normalize_status(self):
        if self.status:
            self.status = self.status.lower().replace("to_do", "todo")
        return self


class TaskRead(BaseSchema):
    id: str
    task_number: str
    title: str
    description: Optional[str] = None
    status: str
    priority: str
    task_type: str = "feature"
    story_points: Optional[int] = None
    due_date: Optional[datetime] = None
    project_id: Optional[str] = None
    assignee_id: Optional[str] = None
    assigned_to_id: Optional[str] = None
    reporter_id: Optional[str] = None
    assigner_id: Optional[str] = None
    ticket_id: Optional[str] = None
    ticket_ids: Optional[List[str]] = None
    organization_id: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    comment_count: int = 0
    attachment_count: int = 0
    tickets_count: int = 0
    open_tickets_count: int = 0
    ticket_count: Optional[int] = None
    assignee: Optional[ActorSchema] = None
    reporter: Optional[ActorSchema] = None
    assigner: Optional[ActorSchema] = None
    permissions: Optional[TaskPermissions] = None

    @model_validator(mode="after")
    def sync_assignee(self):
        if not self.assignee_id and self.assigned_to_id:
            self.assignee_id = self.assigned_to_id
        elif not self.assigned_to_id and self.assignee_id:
            self.assigned_to_id = self.assignee_id
        if not self.assigner_id and self.reporter_id:
            self.assigner_id = self.reporter_id
        elif not self.reporter_id and self.assigner_id:
            self.reporter_id = self.assigner_id
        if not self.assigner and self.reporter:
            self.assigner = self.reporter
        elif not self.reporter and self.assigner:
            self.reporter = self.assigner
        if self.ticket_count is None:
            self.ticket_count = self.tickets_count
        if self.status:
            self.status = self.status.lower().replace("to_do", "todo")
        return self


class TaskTicketListResponse(BaseSchema):
    items: List[TicketRead]
    total: int
    page: int
    page_size: int


class TaskListResponse(BaseSchema):
    items: List[TaskRead]
    total: int
    page: int
    page_size: int
    stats: Optional[Dict[str, int]] = None
    todo_count: Optional[int] = None
    in_progress_count: Optional[int] = None
    done_count: Optional[int] = None
