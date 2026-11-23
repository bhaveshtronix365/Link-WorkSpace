import os
import time
import uuid
import json
import logging
from pathlib import Path
from typing import List, Optional, Dict
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, status
from fastapi.responses import FileResponse
from sqlalchemy import select, desc, or_, func
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.core.config import settings
from backend.app.database import get_db
from backend.app.dependencies import get_current_active_user
from backend.app.models import (
    Discussion,
    DiscussionComment,
    Project,
    ProjectActivity,
    TicketAttachment,
    AuditLog,
    User,
)
from backend.app.schemas import (
    DiscussionCreate,
    DiscussionRead,
    DiscussionCommentCreate,
    DiscussionCommentRead,
    ReactionRequest,
    TicketAttachmentRead,
)
from backend.app.routes.tickets import (
    validate_file_content,
    check_upload_rate_limit,
    _resolve_attachment_path,
    is_org_owner,
)

logger = logging.getLogger(__name__)
router = APIRouter()


async def _get_authorized_discussion(
    discussion_id: str,
    current_user: User,
    db: AsyncSession,
) -> Discussion:
    """Verifies discussion existence and organization authorization from verified membership."""
    res = await db.execute(
        select(Discussion)
        .options(
            selectinload(Discussion.project),
            selectinload(Discussion.author),
        )
        .where(Discussion.id == discussion_id)
    )
    disc = res.scalars().first()
    if not disc or not disc.project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "Discussion not found"}
        )

    active_org_ids = {m.organization_id for m in (current_user.memberships or []) if m.status == "active"}
    if current_user.organization_id:
        active_org_ids.add(current_user.organization_id)

    if disc.project.organization_id not in active_org_ids:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "not_found", "message": "Discussion not found"}
        )

    return disc


def _is_authorized_org(project: Optional[Project], current_user: User) -> bool:
    if not project:
        return False
    active_org_ids = {m.organization_id for m in (current_user.memberships or []) if m.status == "active"}
    if current_user.organization_id:
        active_org_ids.add(current_user.organization_id)
    return project.organization_id in active_org_ids


@router.get("", response_model=List[DiscussionRead])
@router.get("/", response_model=List[DiscussionRead], include_in_schema=False)
async def list_discussions(
    project_id: Optional[str] = Query(None, description="Filter by project ID"),
    category: Optional[str] = Query(None, description="Filter by category (announcement, rfc, question, general)"),
    search: Optional[str] = Query(None, description="Search in title or content"),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """List discussions with authors, comments, and attachments scoped to tenant."""
    query = (
        select(Discussion)
        .options(
            selectinload(Discussion.author),
            selectinload(Discussion.project),
            selectinload(Discussion.attachments).selectinload(TicketAttachment.uploader),
            selectinload(Discussion.comments).selectinload(DiscussionComment.author),
            selectinload(Discussion.comments).selectinload(DiscussionComment.attachments).selectinload(TicketAttachment.uploader),
        )
        .join(Discussion.project)
        .where(Project.organization_id == current_user.organization_id)
    )

    if project_id and project_id.lower() != "all":
        query = query.where(Discussion.project_id == project_id)
    if category and category.lower() != "all":
        query = query.where(Discussion.category == category.lower())
    if search:
        s = f"%{search.strip()}%"
        query = query.where(Discussion.title.ilike(s) | Discussion.content.ilike(s))

    query = query.order_by(desc(Discussion.is_pinned), desc(Discussion.created_at))
    res = await db.execute(query)
    return res.scalars().all()


@router.get("/{discussion_id}", response_model=DiscussionRead)
async def get_discussion(
    discussion_id: str,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """Retrieve a single discussion thread with comments, authors, and attachments."""
    res = await db.execute(
        select(Discussion)
        .options(
            selectinload(Discussion.author),
            selectinload(Discussion.project),
            selectinload(Discussion.attachments).selectinload(TicketAttachment.uploader),
            selectinload(Discussion.comments).selectinload(DiscussionComment.author),
            selectinload(Discussion.comments).selectinload(DiscussionComment.attachments).selectinload(TicketAttachment.uploader),
        )
        .where(Discussion.id == discussion_id)
    )
    disc = res.scalars().first()
    if not disc or not _is_authorized_org(disc.project, current_user):
        raise HTTPException(status_code=404, detail="Discussion not found")
    return disc


@router.post("", response_model=DiscussionRead, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=DiscussionRead, status_code=status.HTTP_201_CREATED, include_in_schema=False)
async def create_discussion(
    disc_in: DiscussionCreate,
    project_id: Optional[str] = Query(None, description="Project ID"),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """Create a new discussion topic or announcement in project within tenant with optional attachments."""
    target_project_id = project_id
    if not target_project_id:
        p_res = await db.execute(
            select(Project)
            .where(Project.organization_id == current_user.organization_id)
            .order_by(Project.created_at)
        )
        proj = p_res.scalars().first()
        if not proj:
            raise HTTPException(status_code=400, detail="No workspace project available in your organization")
        target_project_id = proj.id
    else:
        p_res = await db.execute(
            select(Project).where(
                Project.id == target_project_id,
                Project.organization_id == current_user.organization_id
            )
        )
        if not p_res.scalars().first():
            raise HTTPException(status_code=404, detail="Project not found or not in your organization")

    now = datetime.now(timezone.utc)
    new_disc = Discussion(
        project_id=target_project_id,
        author_id=current_user.id,
        title=disc_in.title.strip(),
        content=disc_in.content.strip(),
        category=(disc_in.category or "general").lower(),
        is_pinned=bool(disc_in.is_pinned),
        reactions=disc_in.reactions or "{}",
        created_at=now,
        updated_at=now,
    )
    db.add(new_disc)
    await db.flush()

    # Link optional attachments (max 5)
    if disc_in.attachment_ids:
        if len(disc_in.attachment_ids) > 5:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "too_many_attachments", "message": "A discussion can have at most 5 attachments."}
            )
        att_res = await db.execute(
            select(TicketAttachment).where(
                TicketAttachment.id.in_(disc_in.attachment_ids),
                TicketAttachment.uploader_id == current_user.id,
            )
        )
        found_atts = att_res.scalars().all()
        for fa in found_atts:
            fa.discussion_id = new_disc.id
            fa.file_url = f"/api/v1/discussions/{new_disc.id}/attachments/{fa.id}/download"

    activity = ProjectActivity(
        project_id=target_project_id,
        actor_id=current_user.id,
        action="discussion_created",
        category="discussions",
        target_type="discussion",
        target_id=new_disc.id,
        target_title=new_disc.title,
        details=f"Category: {new_disc.category}",
        created_at=now,
    )
    db.add(activity)
    await db.commit()

    res = await db.execute(
        select(Discussion)
        .options(
            selectinload(Discussion.author),
            selectinload(Discussion.project),
            selectinload(Discussion.attachments).selectinload(TicketAttachment.uploader),
            selectinload(Discussion.comments).selectinload(DiscussionComment.author),
            selectinload(Discussion.comments).selectinload(DiscussionComment.attachments).selectinload(TicketAttachment.uploader),
        )
        .where(Discussion.id == new_disc.id)
    )
    return res.scalars().first()


@router.post("/{discussion_id}/reactions")
async def toggle_discussion_reaction(
    discussion_id: str,
    payload: ReactionRequest,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """Toggle an emoji reaction on a discussion."""
    res = await db.execute(
        select(Discussion)
        .options(selectinload(Discussion.project))
        .where(Discussion.id == discussion_id)
    )
    disc = res.scalars().first()
    if not disc or not _is_authorized_org(disc.project, current_user):
        raise HTTPException(status_code=404, detail="Discussion not found")

    emoji = payload.emoji.strip()
    try:
        reactions_dict = json.loads(disc.reactions or "{}")
    except Exception:
        reactions_dict = {}

    current_val = reactions_dict.get(emoji, 0)
    reactions_dict[emoji] = current_val + 1

    disc.reactions = json.dumps(reactions_dict)
    await db.commit()
    return {"reactions": reactions_dict}


@router.post("/{discussion_id}/comments", response_model=DiscussionCommentRead, status_code=status.HTTP_201_CREATED)
async def add_comment_to_discussion(
    discussion_id: str,
    comment_in: DiscussionCommentCreate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """Reply or comment on a discussion thread with optional attachments."""
    disc = await _get_authorized_discussion(discussion_id, current_user, db)

    now = datetime.now(timezone.utc)
    new_comment = DiscussionComment(
        discussion_id=discussion_id,
        author_id=current_user.id,
        content=(comment_in.content or "").strip(),
        created_at=now,
    )
    db.add(new_comment)
    disc.updated_at = now
    await db.flush()

    # Link optional attachments (max 5)
    if comment_in.attachment_ids:
        if len(comment_in.attachment_ids) > 5:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "too_many_attachments", "message": "A reply can have at most 5 attachments."}
            )
        att_res = await db.execute(
            select(TicketAttachment).where(
                TicketAttachment.id.in_(comment_in.attachment_ids),
                TicketAttachment.uploader_id == current_user.id,
            )
        )
        found_atts = att_res.scalars().all()
        for fa in found_atts:
            fa.discussion_id = disc.id
            fa.discussion_comment_id = new_comment.id
            fa.file_url = f"/api/v1/discussions/{disc.id}/attachments/{fa.id}/download"

    activity = ProjectActivity(
        project_id=disc.project_id,
        actor_id=current_user.id,
        action="discussion_comment_added",
        category="discussions",
        target_type="discussion",
        target_id=disc.id,
        target_title=disc.title,
        details="Added reply",
        created_at=now,
    )
    db.add(activity)
    await db.commit()

    res = await db.execute(
        select(DiscussionComment)
        .options(
            selectinload(DiscussionComment.author),
            selectinload(DiscussionComment.attachments).selectinload(TicketAttachment.uploader),
        )
        .where(DiscussionComment.id == new_comment.id)
    )
    return res.scalars().first()


# ---------------------------------------------------------------------------
# Attachment Endpoints for Discussions (Reusing Ticket Attachment Security)
# ---------------------------------------------------------------------------

async def _process_upload(
    file: UploadFile,
    current_user: User,
    db: AsyncSession,
    discussion: Optional[Discussion] = None,
) -> TicketAttachment:
    """Reusable secure file upload logic enforcing size, allowlist, and magic bytes."""
    check_upload_rate_limit(current_user.id)

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
    disc_id = discussion.id if discussion else None
    file_url = (
        f"/api/v1/discussions/{disc_id}/attachments/{att_id}/download"
        if disc_id else
        f"/api/v1/discussions/attachments/{att_id}/download"
    )

    att = TicketAttachment(
        id=att_id,
        ticket_id=None,
        discussion_id=disc_id,
        uploader_id=current_user.id,
        filename=safe_name,
        original_name=safe_name,
        stored_name=stored_filename,
        content_type=detected_mime,
        size_bytes=total_bytes,
        file_url=file_url,
        created_at=now,
    )
    db.add(att)

    org_id = discussion.project.organization_id if (discussion and discussion.project) else current_user.organization_id
    audit_entry = AuditLog(
        organization_id=org_id,
        actor_id=current_user.id,
        action="discussion_attachment_uploaded",
        details=json.dumps({
            "name": safe_name,
            "size_bytes": total_bytes,
            "discussion_id": disc_id,
        })
    )
    db.add(audit_entry)
    await db.commit()

    res_att = await db.execute(
        select(TicketAttachment)
        .options(selectinload(TicketAttachment.uploader))
        .where(TicketAttachment.id == att.id)
    )
    return res_att.scalars().first()


@router.post("/attachments", response_model=TicketAttachmentRead, status_code=status.HTTP_201_CREATED)
async def upload_standalone_discussion_attachment(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """Upload attachment from discussion composer prior to thread creation."""
    return await _process_upload(file, current_user, db, discussion=None)


@router.post("/{discussion_id}/attachments", response_model=TicketAttachmentRead, status_code=status.HTTP_201_CREATED)
async def upload_discussion_attachment(
    discussion_id: str,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """Upload attachment attached to an existing discussion thread."""
    if discussion_id in ("new", "composer"):
        return await _process_upload(file, current_user, db, discussion=None)
    discussion = await _get_authorized_discussion(discussion_id, current_user, db)
    return await _process_upload(file, current_user, db, discussion=discussion)


@router.get("/{discussion_id}/attachments", response_model=List[TicketAttachmentRead])
async def list_discussion_attachments(
    discussion_id: str,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """List all attachments belonging to a discussion thread."""
    discussion = await _get_authorized_discussion(discussion_id, current_user, db)
    res = await db.execute(
        select(TicketAttachment)
        .options(selectinload(TicketAttachment.uploader))
        .where(TicketAttachment.discussion_id == discussion.id)
        .order_by(TicketAttachment.created_at.desc())
    )
    return res.scalars().all()


async def _resolve_and_verify_attachment(
    attachment_id: str,
    current_user: User,
    db: AsyncSession,
    discussion_id: Optional[str] = None,
) -> TicketAttachment:
    """Verifies access to discussion attachment by organization membership."""
    query = (
        select(TicketAttachment)
        .options(
            selectinload(TicketAttachment.discussion).selectinload(Discussion.project),
            selectinload(TicketAttachment.uploader),
        )
        .where(
            or_(
                TicketAttachment.id == attachment_id,
                TicketAttachment.file_url.ilike(f"%{attachment_id}%")
            )
        )
    )
    if discussion_id and discussion_id not in ("new", "composer"):
        query = query.where(TicketAttachment.discussion_id == discussion_id)

    res = await db.execute(query)
    att = res.scalars().first()
    if not att:
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "Attachment record not found"}
        )

    # Permission check: if linked to discussion, must have org membership
    if att.discussion and att.discussion.project:
        if not _is_authorized_org(att.discussion.project, current_user):
            raise HTTPException(
                status_code=404,
                detail={"code": "not_found", "message": "Attachment record not found"}
            )
    else:
        # Pre-creation attachment: uploader or same org member
        if att.uploader_id != current_user.id:
            active_org_ids = {m.organization_id for m in (current_user.memberships or []) if m.status == "active"}
            if current_user.organization_id:
                active_org_ids.add(current_user.organization_id)
            if att.uploader and att.uploader.organization_id not in active_org_ids:
                raise HTTPException(
                    status_code=404,
                    detail={"code": "not_found", "message": "Attachment record not found"}
                )

    return att


@router.get("/{discussion_id}/attachments/{attachment_id}/download")
@router.get("/attachments/{attachment_id}/download")
async def download_discussion_attachment(
    attachment_id: str,
    discussion_id: Optional[str] = None,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Authenticated download endpoint:
    - Enforces Content-Disposition: attachment
    - Enforces Content-Type and X-Content-Type-Options: nosniff
    """
    att = await _resolve_and_verify_attachment(attachment_id, current_user, db, discussion_id)
    candidate_path = _resolve_attachment_path(att)
    if not candidate_path or not os.path.exists(candidate_path) or not os.path.isfile(candidate_path):
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "Attachment file not found on storage"}
        )

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


@router.get("/{discussion_id}/attachments/{attachment_id}/preview")
@router.get("/attachments/{attachment_id}/preview")
async def preview_discussion_attachment(
    attachment_id: str,
    discussion_id: Optional[str] = None,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Authenticated inline preview endpoint for image attachments only:
    - Enforces Content-Disposition: inline
    - Enforces Content-Type and X-Content-Type-Options: nosniff
    """
    att = await _resolve_and_verify_attachment(attachment_id, current_user, db, discussion_id)
    if att.content_type not in ("image/png", "image/jpeg", "image/webp", "image/gif"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "preview_not_supported", "message": "Inline preview is only supported for image attachments."}
        )

    candidate_path = _resolve_attachment_path(att)
    if not candidate_path or not os.path.exists(candidate_path) or not os.path.isfile(candidate_path):
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "Attachment file not found on storage"}
        )

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


@router.delete("/{discussion_id}/attachments/{attachment_id}")
@router.delete("/attachments/{attachment_id}")
async def delete_discussion_attachment(
    attachment_id: str,
    discussion_id: Optional[str] = None,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Delete discussion attachment endpoint:
    - Only the uploader and organization owner/admin may delete.
    - Removes stored file from disk and records AuditLog.
    """
    att = await _resolve_and_verify_attachment(attachment_id, current_user, db, discussion_id)

    org_id = (
        att.discussion.project.organization_id
        if (att.discussion and att.discussion.project)
        else current_user.organization_id
    )

    is_owner_or_admin = (
        current_user.role in ("owner", "admin") or
        is_org_owner(current_user, org_id) or
        any(m.organization_id == org_id and m.role in ("owner", "admin") and m.status == "active" for m in (current_user.memberships or []))
    )

    if att.uploader_id != current_user.id and not is_owner_or_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "forbidden", "message": "Only the uploader or organization administrators may delete this attachment."}
        )

    candidate_path = _resolve_attachment_path(att)
    if candidate_path and os.path.exists(candidate_path) and os.path.isfile(candidate_path):
        try:
            os.remove(candidate_path)
        except Exception as e:
            logger.warning(f"Failed to remove attachment file on disk {candidate_path}: {e}")

    await db.delete(att)
    audit = AuditLog(
        organization_id=org_id,
        actor_id=current_user.id,
        action="discussion_attachment_deleted",
        details=json.dumps({"attachment_id": att.id, "filename": att.original_name or att.filename}),
    )
    db.add(audit)
    await db.commit()

    return {"success": True, "message": "Attachment deleted successfully", "id": attachment_id}
