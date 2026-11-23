import React, { useState, useEffect, useCallback, useRef } from 'react';
import { createPortal } from 'react-dom';
import { lockScroll, unlockScroll } from '../../../utils/scrollLock';
import TaskStatusBadge from './TaskStatusBadge';
import TaskPriorityBadge from './TaskPriorityBadge';
import EditTaskAndStatusPopover from './EditTaskAndStatusPopover';
import CommentComposerAndAttachmentPicker from './CommentComposerAndAttachmentPicker';
import Avatar from '../../../components/common/Avatar';
import CreateTicketModal from '../../tickets/components/CreateTicketModal';
import { STATUS_CONFIG, SEVERITY_CONFIG } from '../../tickets/constants/ticketConstants';
import { taskService, connectTasksWebSocket } from '../services/taskService';
import { safeCloseWebSocket } from '../../../services/api';
import Modal from '../../../components/common/Modal';
import {
  TASK_STATUS_OPTIONS,
  TASK_STATUS_LABEL,
  TASK_PRIORITY_OPTIONS,
  TASK_PRIORITY_LABEL,
  TASK_TYPE_OPTIONS,
  TASK_TYPE_LABEL,
} from '../constants/taskEnums';

/**
 * Format timestamp into relative or locale string
 */
const formatTimestamp = (isoString) => {
  if (!isoString) return '';
  const diff = Date.now() - new Date(isoString).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return new Date(isoString).toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
  });
};

/**
 * Format activity entry summary with real names and readable changes
 */
const formatActivitySummary = (act) => {
  const actorName =
    act.actor?.display_name ||
    act.actor?.full_name ||
    act.actor_name ||
    (act.actor_id ? 'Deleted user' : 'System');

  if (act.action === 'created') {
    return `${actorName} created this task`;
  }
  if (act.action === 'status_changed') {
    const fromVal = act.old_value ? TASK_STATUS_LABEL[act.old_value] || act.old_value : 'initial';
    const toVal = act.new_value ? TASK_STATUS_LABEL[act.new_value] || act.new_value : 'updated';
    return `${actorName} changed status from ${fromVal} to ${toVal}`;
  }
  if (act.action === 'assignee_changed') {
    const fromVal =
      act.meta?.old_assignee_name ||
      (act.old_value && !act.old_value.startsWith('usr_') ? act.old_value : null) ||
      'Unassigned';
    const toVal =
      act.meta?.new_assignee_name ||
      (act.new_value && !act.new_value.startsWith('usr_') ? act.new_value : null) ||
      'Unassigned';
    return `${actorName} changed assignee from ${fromVal} to ${toVal}`;
  }
  if (act.action === 'field_changed') {
    const field = act.field_name || 'details';
    let fromVal = act.old_value || 'none';
    let toVal = act.new_value || 'none';

    if (field === 'priority') {
      fromVal = TASK_PRIORITY_LABEL[act.old_value] || act.old_value;
      toVal = TASK_PRIORITY_LABEL[act.new_value] || act.new_value;
    } else if (field === 'task_type') {
      fromVal = TASK_TYPE_LABEL[act.old_value] || act.old_value;
      toVal = TASK_TYPE_LABEL[act.new_value] || act.new_value;
    } else if (field === 'description') {
      fromVal = fromVal.length > 40 ? `${fromVal.slice(0, 40)}...` : fromVal;
      toVal = toVal.length > 40 ? `${toVal.slice(0, 40)}...` : toVal;
    }

    const fieldLabel = field.replace(/_/g, ' ');
    return `${actorName} changed ${fieldLabel} from ${fromVal} to ${toVal}`;
  }
  if (act.action === 'comment_added') {
    return `${actorName} commented on this task`;
  }
  if (act.action === 'comment_edited') {
    return `${actorName} edited a comment`;
  }
  if (act.action === 'comment_deleted') {
    return `${actorName} deleted a comment`;
  }
  if (act.action === 'attachment_added') {
    const fname = act.meta?.filename || act.new_value || 'a file';
    return `${actorName} uploaded attachment ${fname}`;
  }
  if (act.action === 'attachment_removed') {
    const fname = act.meta?.filename || act.old_value || 'a file';
    return `${actorName} removed attachment ${fname}`;
  }
  if (act.action === 'ticket_linked') {
    const tnum = act.meta?.ticket_number || act.new_value || 'a ticket';
    return `${actorName} linked ticket ${tnum}`;
  }
  if (act.action === 'ticket_unlinked') {
    const tnum = act.meta?.ticket_number || act.old_value || 'a ticket';
    return `${actorName} unlinked ticket ${tnum}`;
  }
  if (act.action === 'task_deleted') {
    return `${actorName} deleted the task`;
  }

  return act.summary || `${actorName} updated this task`;
};

/**
 * TaskDetailDrawer (Task 4)
 * Comprehensive task details drawer with:
 * - Server-driven permissions enforcement (can_edit_details, can_change_status, can_delete)
 * - Read-only fields with lock badge when can_edit_details is false
 * - 403 error banner with aria-live and automated task refresh
 * - 4 Contextual Tabs: Discussion, Files, Activity, Tickets with count badges
 * - Activity timeline with WebSocket live stream, pagination, real names & avatars
 * - Tickets tab with linked ticket list, status/severity badges, unlink and Create Ticket modal integration
 */
export const TaskDetailDrawer = ({
  isOpen,
  onClose,
  task: initialTask,
  initialTab = 'discussion',
  onUpdateTask,
  onDeleteTask,
}) => {
  const [task, setTask] = useState(initialTask);
  const [isEditingTitle, setIsEditingTitle] = useState(false);
  const [title, setTitle] = useState(initialTask?.title || '');
  const [description, setDescription] = useState(initialTask?.description || '');
  const [isStatusPopoverOpen, setIsStatusPopoverOpen] = useState(false);
  const [isDeleteDialogOpen, setIsDeleteDialogOpen] = useState(false);
  const [isDeletingTask, setIsDeletingTask] = useState(false);

  // Active drawer tab: 'discussion' | 'files' | 'activity' | 'tickets'
  const [activeTab, setActiveTab] = useState(initialTab || 'discussion');

  // Counts for tabs
  const [commentsCount, setCommentsCount] = useState(initialTask?.comment_count || 0);
  const [attachmentsCount, setAttachmentsCount] = useState(initialTask?.attachment_count || 0);

  // Error state for 403 and other API errors
  const [errorMessage, setErrorMessage] = useState(null);

  // Activity Tab State
  const [activities, setActivities] = useState([]);
  const [activityTotal, setActivityTotal] = useState(0);
  const [activityPage, setActivityPage] = useState(1);
  const [loadingActivity, setLoadingActivity] = useState(false);
  const [loadingMoreActivity, setLoadingMoreActivity] = useState(false);
  const [activityError, setActivityError] = useState(null);

  // Tickets Tab State
  const [linkedTickets, setLinkedTickets] = useState([]);
  const [loadingTickets, setLoadingTickets] = useState(false);
  const [isCreateTicketModalOpen, setIsCreateTicketModalOpen] = useState(false);
  const [unlinkingTicketId, setUnlinkingTicketId] = useState(null);

  // Permissions resolved from server
  const permissions = task?.permissions || {
    can_edit_details: true,
    can_change_status: true,
    can_delete: true,
  };
  const canEditDetails = Boolean(permissions.can_edit_details);
  const canChangeStatus = Boolean(permissions.can_change_status);
  const canDelete = Boolean(permissions.can_delete);

  // Sync initial task and tab
  useEffect(() => {
    if (initialTask) {
      setTask(initialTask);
      setTitle(initialTask.title || '');
      setDescription(initialTask.description || '');
      setCommentsCount(initialTask.comment_count || 0);
      setAttachmentsCount(initialTask.attachment_count || 0);
      setIsEditingTitle(false);
      setErrorMessage(null);
    }
    if (isOpen && initialTab) {
      setActiveTab(initialTab);
    }
  }, [initialTask, isOpen, initialTab]);

  // Lock scroll and handle escape
  useEffect(() => {
    if (!isOpen) return;
    lockScroll();
    const handleKeyDown = (e) => {
      if (e.key === 'Escape' && !isStatusPopoverOpen && !isDeleteDialogOpen && !isCreateTicketModalOpen) {
        onClose();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => {
      unlockScroll();
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [isOpen, onClose, isStatusPopoverOpen, isDeleteDialogOpen, isCreateTicketModalOpen]);

  // Refresh task helper (called on 403 or after permission changes)
  const refreshTask = useCallback(async () => {
    if (!task?.id) return;
    try {
      const refreshed = await taskService.get(task.id);
      if (refreshed) {
        setTask(refreshed);
        setTitle(refreshed.title || '');
        setDescription(refreshed.description || '');
        setCommentsCount(refreshed.comment_count || 0);
        setAttachmentsCount(refreshed.attachment_count || 0);
        onUpdateTask?.(task.id, refreshed);
      }
    } catch (err) {
      console.warn('Failed to refresh task:', err);
    }
  }, [task?.id, onUpdateTask]);

  // Handle action errors (especially 403 task_edit_forbidden)
  const handleActionError = useCallback(
    async (err, defaultMsg = 'Action failed') => {
      const status = err.response?.status;
      const detail = err.response?.data?.detail;
      const code = err.response?.data?.code;

      if (status === 403 || code === 'task_edit_forbidden') {
        setErrorMessage(detail || 'Only the task creator or the owner can edit these details.');
        await refreshTask();
      } else {
        setErrorMessage(detail || defaultMsg);
      }
    },
    [refreshTask]
  );

  // Fetch activities
  const fetchActivities = useCallback(
    async (page = 1, append = false) => {
      if (!task?.id) return;
      if (page === 1) setLoadingActivity(true);
      else setLoadingMoreActivity(true);
      setActivityError(null);

      try {
        const res = await taskService.getActivity(task.id, page, 20);
        const items = res?.items || [];
        setActivities((prev) => (append ? [...prev, ...items] : items));
        setActivityTotal(res?.total ?? items.length);
        setActivityPage(page);
      } catch (err) {
        setActivityError('Failed to load task activity timeline.');
      } finally {
        setLoadingActivity(false);
        setLoadingMoreActivity(false);
      }
    },
    [task?.id]
  );

  // Fetch linked tickets
  const fetchLinkedTickets = useCallback(async () => {
    if (!task?.id) return;
    setLoadingTickets(true);
    try {
      const list = await taskService.listLinkedTickets(task.id);
      setLinkedTickets(list || []);
    } catch (err) {
      console.error('Failed to load linked tickets:', err);
    } finally {
      setLoadingTickets(false);
    }
  }, [task?.id]);

  // Load tab-specific data when tab changes or drawer opens
  useEffect(() => {
    if (!isOpen || !task?.id) return;

    if (activeTab === 'activity') {
      fetchActivities(1, false);
    } else if (activeTab === 'tickets') {
      fetchLinkedTickets();
    }
  }, [isOpen, task?.id, activeTab, fetchActivities, fetchLinkedTickets]);

  // Initial load of linked tickets count
  useEffect(() => {
    if (isOpen && task?.id) {
      taskService.listLinkedTickets(task.id).then((list) => {
        setLinkedTickets(list || []);
      });
    }
  }, [isOpen, task?.id]);

  // Real-time WebSocket listener for task activities
  useEffect(() => {
    if (!isOpen || !task?.id) return;

    const ws = connectTasksWebSocket((msg) => {
      if (!msg) return;

      // Handle task activity push
      if (msg.event === 'task_activity' && msg.activity) {
        if (msg.activity.task_id === task.id) {
          setActivities((prev) => [msg.activity, ...prev]);
          setActivityTotal((prev) => prev + 1);

          // If ticket linked/unlinked, update tickets list
          if (
            msg.activity.action === 'ticket_linked' ||
            msg.activity.action === 'ticket_unlinked'
          ) {
            fetchLinkedTickets();
          }
        }
      }

      // Handle task updates
      if (msg.event === 'task_updated' && msg.task_id === task.id && msg.changes) {
        setTask((prev) => ({ ...prev, ...msg.changes }));
      }
    });

    return () => {
      if (ws) safeCloseWebSocket(ws);
    };
  }, [isOpen, task?.id, fetchLinkedTickets]);

  if (!isOpen || !task) return null;

  // Title Save
  const handleTitleBlur = async () => {
    setIsEditingTitle(false);
    if (!canEditDetails) return;
    if (title.trim() && title !== task.title) {
      try {
        setErrorMessage(null);
        await onUpdateTask?.(task.id, { title: title.trim() });
      } catch (err) {
        handleActionError(err, 'Failed to update title.');
        setTitle(task.title || '');
      }
    } else {
      setTitle(task.title || '');
    }
  };

  // Description Save
  const handleDescriptionBlur = async () => {
    if (!canEditDetails) return;
    if (description !== (task.description || '')) {
      try {
        setErrorMessage(null);
        await onUpdateTask?.(task.id, { description });
      } catch (err) {
        handleActionError(err, 'Failed to update description.');
        setDescription(task.description || '');
      }
    }
  };

  // Priority Change
  const handlePriorityChange = async (e) => {
    if (!canEditDetails) return;
    const newPriority = e.target.value;
    if (newPriority && newPriority !== task.priority) {
      try {
        setErrorMessage(null);
        await onUpdateTask?.(task.id, { priority: newPriority });
      } catch (err) {
        handleActionError(err, 'Failed to update priority.');
      }
    }
  };

  // Type Change
  const handleTypeChange = async (e) => {
    if (!canEditDetails) return;
    const newType = e.target.value;
    if (newType && newType !== task.task_type) {
      try {
        setErrorMessage(null);
        await onUpdateTask?.(task.id, { task_type: newType });
      } catch (err) {
        handleActionError(err, 'Failed to update task type.');
      }
    }
  };

  // Story Points Change
  const handlePointsChange = async (e) => {
    if (!canEditDetails) return;
    const val = e.target.value === '' ? null : Number(e.target.value);
    try {
      setErrorMessage(null);
      await onUpdateTask?.(task.id, { story_points: val });
    } catch (err) {
      handleActionError(err, 'Failed to update story points.');
    }
  };

  // Due Date Change
  const handleDueDateChange = async (e) => {
    if (!canEditDetails) return;
    const val = e.target.value ? new Date(e.target.value).toISOString() : null;
    try {
      setErrorMessage(null);
      await onUpdateTask?.(task.id, { due_date: val });
    } catch (err) {
      handleActionError(err, 'Failed to update due date.');
    }
  };

  // Status Change
  const handleStatusChange = async (newStatus) => {
    if (!canChangeStatus) return;
    try {
      setErrorMessage(null);
      await onUpdateTask?.(task.id, { status: newStatus });
    } catch (err) {
      handleActionError(err, 'Failed to change task status.');
    }
  };

  // Task Delete
  const handleConfirmDeleteTask = async () => {
    if (!canDelete) return;
    setIsDeletingTask(true);
    try {
      await onDeleteTask?.(task.id);
      setIsDeleteDialogOpen(false);
      onClose();
    } catch (err) {
      handleActionError(err, 'Failed to delete task.');
      setIsDeletingTask(false);
    }
  };

  // Unlink Ticket
  const handleUnlinkTicket = async (ticketId) => {
    setUnlinkingTicketId(ticketId);
    try {
      await taskService.unlinkTicket(task.id, ticketId);
      setLinkedTickets((prev) => prev.filter((t) => t.id !== ticketId));
      fetchActivities(1, false);
    } catch (err) {
      handleActionError(err, 'Failed to unlink ticket.');
    } finally {
      setUnlinkingTicketId(null);
    }
  };

  // Assignee display names (strictly real names, never raw ids)
  const assigneeName = task.assignee?.full_name || task.assignee?.display_name || (task.assignee_id ? 'Assigned' : 'Unassigned');
  const assignerName = (task.assigner || task.reporter)?.full_name || (task.assigner || task.reporter)?.display_name || '—';

  return createPortal(
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/30 backdrop-blur-xs z-50 transition-opacity"
        onClick={onClose}
        aria-hidden="true"
      />

      {/* Drawer Container */}
      <div
        className="fixed inset-y-0 right-0 z-50 w-full max-w-2xl bg-surface-card shadow-2xl border-l border-border-subtle flex flex-col overflow-hidden animate-in slide-in-from-right duration-200"
        role="dialog"
        aria-label="Task Details"
      >
        {/* Top Header */}
        <div className="px-6 py-4 border-b border-border-subtle flex items-center justify-between bg-canvas-main/60">
          <div className="flex items-center gap-3">
            <span className="font-mono text-label-md font-bold text-primary bg-primary-fixed px-2.5 py-1 rounded-md">
              {task.task_number}
            </span>
            <div
              onClick={() => {
                if (canChangeStatus) setIsStatusPopoverOpen(true);
              }}
              className={canChangeStatus ? 'cursor-pointer' : 'cursor-default opacity-90'}
              title={canChangeStatus ? 'Click to change status' : 'Status cannot be changed by current user'}
            >
              <TaskStatusBadge status={task.status} interactive={canChangeStatus} />
            </div>
          </div>

          <div className="flex items-center gap-1">
            {/* Delete button (hidden if can_delete is false) */}
            {canDelete && (
              <button
                type="button"
                onClick={() => setIsDeleteDialogOpen(true)}
                title="Delete task"
                className="w-8 h-8 rounded-lg hover:bg-error-container/40 text-text-muted hover:text-error flex items-center justify-center transition-colors"
                aria-label="Delete task"
              >
                <span className="material-symbols-outlined text-[18px]">delete</span>
              </button>
            )}
            <button
              type="button"
              onClick={onClose}
              title="Close drawer"
              className="w-8 h-8 rounded-lg hover:bg-canvas-lavender text-text-muted hover:text-text-primary flex items-center justify-center transition-colors"
              aria-label="Close drawer"
            >
              <span className="material-symbols-outlined text-[20px]">close</span>
            </button>
          </div>
        </div>

        {/* Scrollable Body */}
        <div
          className="flex-1 overflow-y-auto p-6 space-y-6 overscroll-contain"
          style={{ overscrollBehavior: 'contain' }}
        >
          {/* 403 / Error Banner with aria-live */}
          {errorMessage && (
            <div
              role="alert"
              aria-live="assertive"
              className="flex items-start justify-between gap-3 p-3.5 rounded-xl bg-error-container/30 border border-error/20 text-error text-body-sm animate-in fade-in"
            >
              <div className="flex items-start gap-2">
                <span className="material-symbols-outlined text-[18px] shrink-0 mt-0.5">error</span>
                <span>{errorMessage}</span>
              </div>
              <button
                type="button"
                onClick={() => setErrorMessage(null)}
                className="text-error/70 hover:text-error font-semibold text-xs"
                aria-label="Dismiss error"
              >
                Dismiss
              </button>
            </div>
          )}

          {/* Read-Only Notice when can_edit_details is false */}
          {!canEditDetails && (
            <div className="flex items-center gap-2 px-3.5 py-2.5 rounded-xl bg-amber-500/10 text-amber-800 dark:text-amber-200 text-xs font-medium border border-amber-500/20">
              <span className="material-symbols-outlined text-[16px] shrink-0">lock</span>
              <span>Only the task creator or the owner can edit these details.</span>
            </div>
          )}

          {/* Title Field (Permissions-aware) */}
          <div>
            {canEditDetails ? (
              isEditingTitle ? (
                <input
                  type="text"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  onBlur={handleTitleBlur}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') handleTitleBlur();
                    if (e.key === 'Escape') {
                      setTitle(task.title || '');
                      setIsEditingTitle(false);
                    }
                  }}
                  autoFocus
                  className="w-full font-headline-md text-headline-md text-text-primary px-2 py-1 border border-primary rounded-lg focus:outline-none"
                  aria-label="Task title"
                />
              ) : (
                <h2
                  onClick={() => setIsEditingTitle(true)}
                  className="font-headline-md text-headline-md text-text-primary hover:bg-canvas-lavender/50 px-2 py-1 rounded-lg cursor-text transition-colors"
                  title="Click to edit title"
                  tabIndex={0}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      setIsEditingTitle(true);
                    }
                  }}
                >
                  {task.title}
                </h2>
              )
            ) : (
              <h2 className="font-headline-md text-headline-md text-text-primary px-2 py-1">
                {task.title}
              </h2>
            )}
          </div>

          {/* Properties Grid (Permissions-aware) */}
          <div className="bg-canvas-main/80 rounded-2xl p-4 border border-border-subtle grid grid-cols-2 gap-4">
            {/* Priority */}
            <div>
              <label className="block text-[11px] font-semibold text-text-muted uppercase tracking-wider mb-1">
                Priority
              </label>
              {canEditDetails ? (
                <select
                  value={task.priority || ''}
                  onChange={handlePriorityChange}
                  className="w-full h-9 px-2.5 rounded-lg border border-border-subtle bg-surface-card text-text-primary font-label-md focus:outline-none focus:ring-2 focus:ring-primary/20"
                >
                  <option value="" disabled>Select priority</option>
                  {TASK_PRIORITY_OPTIONS.map((pr) => (
                    <option key={pr} value={pr}>
                      {TASK_PRIORITY_LABEL[pr]}
                    </option>
                  ))}
                </select>
              ) : (
                <div className="h-9 px-2.5 flex items-center">
                  <TaskPriorityBadge priority={task.priority} />
                </div>
              )}
            </div>

            {/* Type */}
            <div>
              <label className="block text-[11px] font-semibold text-text-muted uppercase tracking-wider mb-1">
                Type
              </label>
              {canEditDetails ? (
                <select
                  value={task.task_type || ''}
                  onChange={handleTypeChange}
                  className="w-full h-9 px-2.5 rounded-lg border border-border-subtle bg-surface-card text-text-primary font-label-md focus:outline-none focus:ring-2 focus:ring-primary/20"
                >
                  <option value="" disabled>Select type</option>
                  {TASK_TYPE_OPTIONS.map((tp) => (
                    <option key={tp} value={tp}>
                      {TASK_TYPE_LABEL[tp]}
                    </option>
                  ))}
                </select>
              ) : (
                <div className="h-9 px-2.5 flex items-center font-label-md text-label-md text-text-primary">
                  {TASK_TYPE_LABEL[task.task_type] || task.task_type || '—'}
                </div>
              )}
            </div>

            {/* Story Points */}
            <div>
              <label className="block text-[11px] font-semibold text-text-muted uppercase tracking-wider mb-1">
                Story Points
              </label>
              {canEditDetails ? (
                <input
                  type="number"
                  min="0"
                  max="100"
                  value={task.story_points ?? ''}
                  onChange={handlePointsChange}
                  placeholder="0"
                  className="w-full h-9 px-2.5 rounded-lg border border-border-subtle bg-surface-card text-text-primary font-body-sm focus:outline-none focus:ring-2 focus:ring-primary/20"
                />
              ) : (
                <div className="h-9 px-2.5 flex items-center font-body-sm text-body-sm text-text-primary">
                  {task.story_points ?? '—'}
                </div>
              )}
            </div>

            {/* Due Date */}
            <div>
              <label className="block text-[11px] font-semibold text-text-muted uppercase tracking-wider mb-1">
                Due Date
              </label>
              {canEditDetails ? (
                <input
                  type="date"
                  value={task.due_date ? task.due_date.slice(0, 10) : ''}
                  onChange={handleDueDateChange}
                  className="w-full h-9 px-2.5 rounded-lg border border-border-subtle bg-surface-card text-text-primary font-body-sm focus:outline-none focus:ring-2 focus:ring-primary/20"
                />
              ) : (
                <div className="h-9 px-2.5 flex items-center font-body-sm text-body-sm text-text-primary">
                  {task.due_date
                    ? new Date(task.due_date).toLocaleDateString(undefined, {
                        year: 'numeric',
                        month: 'short',
                        day: 'numeric',
                      })
                    : '—'}
                </div>
              )}
            </div>

            {/* Assignee */}
            <div>
              <label className="block text-[11px] font-semibold text-text-muted uppercase tracking-wider mb-1">
                Assignee
              </label>
              <div className="h-9 px-2.5 flex items-center gap-2 min-w-0">
                {task.assignee ? (
                  <>
                    <Avatar
                      src={task.assignee.avatar_url}
                      name={assigneeName}
                      size="xs"
                      shape="rounded-full"
                    />
                    <span className="font-label-md text-label-md text-text-primary truncate">
                      {assigneeName}
                    </span>
                  </>
                ) : (
                  <span className="font-body-sm text-body-sm text-text-muted">Unassigned</span>
                )}
              </div>
            </div>

            {/* Assigner */}
            <div>
              <label className="block text-[11px] font-semibold text-text-muted uppercase tracking-wider mb-1">
                Assigner
              </label>
              <div className="h-9 px-2.5 flex items-center gap-2 min-w-0">
                {task.assigner || task.reporter ? (
                  <>
                    <Avatar
                      src={(task.assigner || task.reporter).avatar_url}
                      name={assignerName}
                      size="xs"
                      shape="rounded-full"
                    />
                    <span className="font-label-md text-label-md text-text-primary truncate">
                      {assignerName}
                    </span>
                  </>
                ) : (
                  <span className="font-body-sm text-body-sm text-text-muted">—</span>
                )}
              </div>
            </div>
          </div>

          {/* Description Section (Permissions-aware) */}
          <div className="space-y-2">
            <label className="block font-label-md text-label-md text-text-secondary">
              Description
            </label>
            {canEditDetails ? (
              <textarea
                rows={4}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                onBlur={handleDescriptionBlur}
                placeholder="Add detailed task description, acceptance criteria, or technical notes..."
                className="w-full p-3 rounded-xl border border-border-subtle bg-surface-card text-text-primary font-body-sm placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-primary/20 resize-y"
              />
            ) : (
              <div className="p-3 rounded-xl bg-canvas-main/40 text-text-primary font-body-sm whitespace-pre-wrap min-h-[5rem]">
                {task.description ? task.description : <span className="text-text-muted italic">No description provided.</span>}
              </div>
            )}
          </div>

          {/* 4 Contextual Tabs Navigation */}
          <div className="pt-4 border-t border-border-subtle">
            <div className="flex items-center gap-2 border-b border-border-subtle pb-2 overflow-x-auto">
              {/* Discussion Tab */}
              <button
                type="button"
                onClick={() => setActiveTab('discussion')}
                className={`font-label-md text-label-md flex items-center gap-1.5 px-3 py-1.5 rounded-lg transition-colors whitespace-nowrap ${
                  activeTab === 'discussion'
                    ? 'bg-canvas-lavender text-primary font-bold'
                    : 'text-text-secondary hover:text-text-primary hover:bg-canvas-main'
                }`}
              >
                <span className="material-symbols-outlined text-[16px]">forum</span>
                <span>Discussion</span>
                {commentsCount > 0 && (
                  <span className="px-1.5 py-0.2 rounded-full text-[11px] bg-primary/10 text-primary font-semibold">
                    {commentsCount}
                  </span>
                )}
              </button>

              {/* Files Tab */}
              <button
                type="button"
                onClick={() => setActiveTab('files')}
                className={`font-label-md text-label-md flex items-center gap-1.5 px-3 py-1.5 rounded-lg transition-colors whitespace-nowrap ${
                  activeTab === 'files'
                    ? 'bg-canvas-lavender text-primary font-bold'
                    : 'text-text-secondary hover:text-text-primary hover:bg-canvas-main'
                }`}
              >
                <span className="material-symbols-outlined text-[16px]">attach_file</span>
                <span>Files</span>
                {attachmentsCount > 0 && (
                  <span className="px-1.5 py-0.2 rounded-full text-[11px] bg-primary/10 text-primary font-semibold">
                    {attachmentsCount}
                  </span>
                )}
              </button>

              {/* Activity Tab */}
              <button
                type="button"
                onClick={() => setActiveTab('activity')}
                className={`font-label-md text-label-md flex items-center gap-1.5 px-3 py-1.5 rounded-lg transition-colors whitespace-nowrap ${
                  activeTab === 'activity'
                    ? 'bg-canvas-lavender text-primary font-bold'
                    : 'text-text-secondary hover:text-text-primary hover:bg-canvas-main'
                }`}
              >
                <span className="material-symbols-outlined text-[16px]">history</span>
                <span>Activity</span>
                {activityTotal > 0 && (
                  <span className="px-1.5 py-0.2 rounded-full text-[11px] bg-canvas-lavender text-text-secondary font-semibold">
                    {activityTotal}
                  </span>
                )}
              </button>

              {/* Tickets Tab */}
              <button
                type="button"
                onClick={() => setActiveTab('tickets')}
                className={`font-label-md text-label-md flex items-center gap-1.5 px-3 py-1.5 rounded-lg transition-colors whitespace-nowrap ${
                  activeTab === 'tickets'
                    ? 'bg-canvas-lavender text-primary font-bold'
                    : 'text-text-secondary hover:text-text-primary hover:bg-canvas-main'
                }`}
              >
                <span className="material-symbols-outlined text-[16px]">confirmation_number</span>
                <span>Tickets</span>
                {linkedTickets.length > 0 && (
                  <span className="px-1.5 py-0.2 rounded-full text-[11px] bg-secondary-fixed text-secondary font-semibold">
                    {linkedTickets.length}
                  </span>
                )}
              </button>
            </div>

            {/* TAB CONTENTS */}
            <div className="pt-4">
              {/* Discussion Tab Content */}
              {activeTab === 'discussion' && (
                <CommentComposerAndAttachmentPicker
                  taskId={task.id}
                  currentTab="comments"
                  hideTabBar={true}
                  onCountsChange={({ commentsCount: cCount, attachmentsCount: aCount }) => {
                    setCommentsCount(cCount);
                    setAttachmentsCount(aCount);
                  }}
                  onCommentAdded={(newComment) => {
                    setCommentsCount((prev) => prev + 1);
                    onUpdateTask?.(task.id, {
                      comment_count: (task.comment_count || 0) + 1,
                    });
                  }}
                  onAttachmentAdded={(newAtt) => {
                    setAttachmentsCount((prev) => prev + 1);
                    onUpdateTask?.(task.id, {
                      attachment_count: (task.attachment_count || 0) + 1,
                    });
                  }}
                />
              )}

              {/* Files Tab Content */}
              {activeTab === 'files' && (
                <CommentComposerAndAttachmentPicker
                  taskId={task.id}
                  currentTab="attachments"
                  hideTabBar={true}
                  onCountsChange={({ commentsCount: cCount, attachmentsCount: aCount }) => {
                    setCommentsCount(cCount);
                    setAttachmentsCount(aCount);
                  }}
                  onAttachmentAdded={(newAtt) => {
                    setAttachmentsCount((prev) => prev + 1);
                    onUpdateTask?.(task.id, {
                      attachment_count: (task.attachment_count || 0) + 1,
                    });
                  }}
                  onAttachmentDeleted={() => {
                    setAttachmentsCount((prev) => Math.max(0, prev - 1));
                    onUpdateTask?.(task.id, {
                      attachment_count: Math.max(0, (task.attachment_count || 1) - 1),
                    });
                  }}
                />
              )}

              {/* Activity Tab Content */}
              {activeTab === 'activity' && (
                <div className="space-y-4">
                  {/* Loading State */}
                  {loadingActivity && (
                    <div className="text-center py-8 text-text-muted font-body-sm flex items-center justify-center gap-2">
                      <span className="material-symbols-outlined text-[20px] animate-spin">refresh</span>
                      <span>Loading activity timeline...</span>
                    </div>
                  )}

                  {/* Error State with Retry */}
                  {!loadingActivity && activityError && (
                    <div
                      role="alert"
                      aria-live="assertive"
                      className="p-4 rounded-xl bg-error-container/20 border border-error/20 text-center space-y-2"
                    >
                      <p className="font-body-sm text-body-sm text-error">{activityError}</p>
                      <button
                        type="button"
                        onClick={() => fetchActivities(1, false)}
                        className="px-3 py-1.5 rounded-lg bg-surface-card border border-border-subtle text-text-primary text-xs hover:bg-canvas-lavender"
                      >
                        Retry
                      </button>
                    </div>
                  )}

                  {/* Empty State */}
                  {!loadingActivity && !activityError && activities.length === 0 && (
                    <div className="text-center py-8 px-4 rounded-xl border border-dashed border-border-subtle bg-canvas-main">
                      <span className="material-symbols-outlined text-[28px] text-text-muted">history</span>
                      <p className="font-label-md text-label-md text-text-primary mt-1">No activity recorded yet</p>
                      <p className="font-body-sm text-body-sm text-text-secondary">
                        Changes to fields, comments, attachments, and status will appear here.
                      </p>
                    </div>
                  )}

                  {/* Activity Timeline */}
                  {!loadingActivity && activities.length > 0 && (
                    <div className="relative pl-6 space-y-5 before:absolute before:left-2 before:top-2 before:bottom-2 before:w-0.5 before:bg-border-subtle">
                      {activities.map((act) => {
                        const actorName =
                          act.actor?.display_name ||
                          act.actor?.full_name ||
                          act.actor_name ||
                          (act.actor_id ? 'Deleted user' : 'System');
                        const actorAvatar = act.actor?.avatar_url || act.actor_avatar;
                        const summary = formatActivitySummary(act);
                        const absTime = act.created_at
                          ? new Date(act.created_at).toLocaleString()
                          : '';
                        const relTime = formatTimestamp(act.created_at);

                        // Select marker icon
                        let markerIcon = 'edit';
                        if (act.action === 'created') markerIcon = 'add_circle';
                        else if (act.action === 'status_changed') markerIcon = 'sync_alt';
                        else if (act.action?.includes('comment')) markerIcon = 'forum';
                        else if (act.action?.includes('attachment')) markerIcon = 'attach_file';
                        else if (act.action?.includes('ticket')) markerIcon = 'confirmation_number';
                        else if (act.action === 'task_deleted') markerIcon = 'delete';

                        return (
                          <div key={act.id} className="relative group">
                            {/* Dot on timeline */}
                            <div className="absolute -left-6 top-1 w-4 h-4 rounded-full bg-surface-card border-2 border-primary flex items-center justify-center">
                              <span className="w-1.5 h-1.5 rounded-full bg-primary" />
                            </div>

                            <div className="bg-surface-card rounded-xl border border-border-subtle p-3 space-y-1 hover:border-primary/30 transition-colors shadow-2xs">
                              <div className="flex items-center justify-between">
                                <div className="flex items-center gap-2">
                                  <Avatar
                                    src={actorAvatar}
                                    name={actorName}
                                    size="xs"
                                    shape="rounded-full"
                                  />
                                  <span className="font-label-md text-label-md text-text-primary font-semibold">
                                    {actorName}
                                  </span>
                                  {act.actor?.role && (
                                    <span className="px-1.5 py-0.5 rounded bg-canvas-lavender text-text-secondary text-[10px] uppercase font-bold tracking-wider">
                                      {act.actor.role}
                                    </span>
                                  )}
                                </div>
                                <span
                                  className="font-label-sm text-label-sm text-text-muted cursor-help"
                                  title={absTime}
                                >
                                  {relTime}
                                </span>
                              </div>

                              <div className="flex items-start gap-2 pl-8 pt-0.5">
                                <span className="material-symbols-outlined text-[15px] text-text-muted mt-0.5 shrink-0">
                                  {markerIcon}
                                </span>
                                <p className="font-body-sm text-body-sm text-text-primary">
                                  {summary}
                                </p>
                              </div>
                            </div>
                          </div>
                        );
                      })}

                      {/* Pagination: Load More */}
                      {activities.length < activityTotal && (
                        <div className="pt-2 text-center">
                          <button
                            type="button"
                            onClick={() => fetchActivities(activityPage + 1, true)}
                            disabled={loadingMoreActivity}
                            className="px-4 py-2 rounded-xl border border-border-subtle bg-surface-card text-text-primary hover:bg-canvas-lavender font-label-md text-label-md transition-all flex items-center gap-1.5 mx-auto disabled:opacity-50"
                          >
                            {loadingMoreActivity ? (
                              <>
                                <span className="material-symbols-outlined text-[16px] animate-spin">refresh</span>
                                <span>Loading more...</span>
                              </>
                            ) : (
                              <>
                                <span className="material-symbols-outlined text-[16px]">expand_more</span>
                                <span>Load more ({activityTotal - activities.length} remaining)</span>
                              </>
                            )}
                          </button>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}

              {/* Tickets Tab Content */}
              {activeTab === 'tickets' && (
                <div className="space-y-4">
                  {/* Action Header */}
                  <div className="flex items-center justify-between">
                    <div>
                      <h3 className="font-label-md text-label-md text-text-primary font-bold">
                        Linked Tickets
                      </h3>
                      <p className="font-body-sm text-body-sm text-text-secondary">
                        Incidents, defects, or tasks associated with this task.
                      </p>
                    </div>
                    <button
                      type="button"
                      onClick={() => setIsCreateTicketModalOpen(true)}
                      className="px-3 py-1.5 rounded-xl bg-btn-primary hover:bg-primary-hover text-on-primary font-label-md text-label-md transition-all flex items-center gap-1.5 shadow-xs"
                    >
                      <span className="material-symbols-outlined text-[16px]">add</span>
                      <span>Create ticket for this task</span>
                    </button>
                  </div>

                  {/* Loading Tickets */}
                  {loadingTickets && (
                    <div className="text-center py-8 text-text-muted font-body-sm flex items-center justify-center gap-2">
                      <span className="material-symbols-outlined text-[18px] animate-spin">refresh</span>
                      <span>Loading linked tickets...</span>
                    </div>
                  )}

                  {/* Empty State */}
                  {!loadingTickets && linkedTickets.length === 0 && (
                    <div className="text-center py-8 px-4 rounded-xl border border-dashed border-border-subtle bg-canvas-main">
                      <span className="material-symbols-outlined text-[28px] text-text-muted">confirmation_number</span>
                      <p className="font-label-md text-label-md text-text-primary mt-1">No tickets linked to this task</p>
                      <p className="font-body-sm text-body-sm text-text-secondary mb-3">
                        Link existing issues or file a new ticket directly for this task.
                      </p>
                      <button
                        type="button"
                        onClick={() => setIsCreateTicketModalOpen(true)}
                        className="px-3.5 py-1.5 rounded-xl bg-surface-card border border-border-subtle hover:bg-canvas-lavender text-text-primary font-label-md text-label-md transition-all inline-flex items-center gap-1.5"
                      >
                        <span className="material-symbols-outlined text-[16px]">add</span>
                        <span>Create ticket for this task</span>
                      </button>
                    </div>
                  )}

                  {/* Linked Tickets List */}
                  {!loadingTickets && linkedTickets.length > 0 && (
                    <div className="space-y-2.5">
                      {linkedTickets.map((t) => {
                        const sevKey = (t.severity || '').toLowerCase();
                        const stKey = (t.status || '').toLowerCase();
                        const sevConfig = SEVERITY_CONFIG[sevKey] || SEVERITY_CONFIG.minor || {};
                        const stConfig = STATUS_CONFIG[stKey] || STATUS_CONFIG.open || {};
                        const tAssigneeName =
                          t.assignee?.full_name ||
                          t.assignee?.display_name ||
                          (t.assignee_id ? 'Assigned' : 'Unassigned');

                        return (
                          <div
                            key={t.id}
                            className="flex items-center justify-between p-3 rounded-xl bg-surface-card border border-border-subtle hover:border-primary/40 transition-colors shadow-2xs"
                          >
                            <div className="flex items-center gap-3 min-w-0">
                              <span className="font-mono text-label-sm font-bold text-primary bg-primary-fixed px-2 py-0.5 rounded shrink-0">
                                {t.ticket_number || `TCK-${t.id.slice(0, 4)}`}
                              </span>

                              <div className="min-w-0">
                                <a
                                  href={`/tickets?ticketId=${t.id}`}
                                  className="font-label-md text-label-md text-text-primary hover:text-primary transition-colors truncate block"
                                  title={t.title}
                                >
                                  {t.title}
                                </a>

                                <div className="flex items-center gap-2 mt-1">
                                  {/* Severity Badge */}
                                  <span
                                    className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-semibold ${
                                      sevConfig.bgBadge || 'bg-canvas-main'
                                    } ${sevConfig.textBadge || 'text-text-secondary'}`}
                                  >
                                    <span
                                      className={`w-1.5 h-1.5 rounded-full ${
                                        sevConfig.dot || 'bg-text-secondary'
                                      }`}
                                    />
                                    {sevConfig.label || t.severity}
                                  </span>

                                  {/* Status Badge */}
                                  <span
                                    className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium ${
                                      stConfig.bgBadge || 'bg-canvas-main'
                                    } ${stConfig.textBadge || 'text-text-secondary'}`}
                                  >
                                    {stConfig.label || t.status}
                                  </span>

                                  {/* Assignee */}
                                  <div className="flex items-center gap-1 text-[11px] text-text-secondary pl-1 border-l border-border-subtle">
                                    <Avatar
                                      src={t.assignee?.avatar_url}
                                      name={tAssigneeName}
                                      size="xs"
                                      shape="rounded-full"
                                    />
                                    <span className="truncate max-w-[100px]">{tAssigneeName}</span>
                                  </div>
                                </div>
                              </div>
                            </div>

                            {/* Unlink Action Button */}
                            <button
                              type="button"
                              onClick={() => handleUnlinkTicket(t.id)}
                              disabled={unlinkingTicketId === t.id}
                              title="Unlink ticket from task"
                              className="w-8 h-8 rounded-lg hover:bg-canvas-lavender text-text-muted hover:text-text-primary flex items-center justify-center transition-colors shrink-0 disabled:opacity-50"
                              aria-label={`Unlink ticket ${t.ticket_number}`}
                            >
                              {unlinkingTicketId === t.id ? (
                                <span className="material-symbols-outlined text-[16px] animate-spin">refresh</span>
                              ) : (
                                <span className="material-symbols-outlined text-[18px]">link_off</span>
                              )}
                            </button>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Status Popover modal */}
      {isStatusPopoverOpen && (
        <EditTaskAndStatusPopover
          isOpen={isStatusPopoverOpen}
          onClose={() => setIsStatusPopoverOpen(false)}
          task={task}
          onStatusChange={handleStatusChange}
          variant="default"
        />
      )}

      {/* Delete Confirmation Modal */}
      {isDeleteDialogOpen && (
        <Modal
          isOpen={isDeleteDialogOpen}
          onClose={() => setIsDeleteDialogOpen(false)}
          title="Delete Task"
          subtitle={`Are you sure you want to delete task ${task.task_number}? This action cannot be undone.`}
          size="sm"
          footer={
            <div className="flex items-center justify-end gap-2 w-full">
              <button
                type="button"
                onClick={() => setIsDeleteDialogOpen(false)}
                className="px-4 py-2 rounded-xl border border-border-subtle text-text-secondary hover:bg-canvas-lavender font-label-md"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleConfirmDeleteTask}
                disabled={isDeletingTask}
                className="px-4 py-2 rounded-xl bg-error hover:bg-error/90 text-white font-label-md flex items-center gap-1.5 disabled:opacity-50"
              >
                {isDeletingTask ? (
                  <>
                    <span className="material-symbols-outlined text-[16px] animate-spin">refresh</span>
                    <span>Deleting...</span>
                  </>
                ) : (
                  <span>Delete</span>
                )}
              </button>
            </div>
          }
        >
          <div className="space-y-2 text-body-sm text-text-secondary">
            <p>
              Task: <strong className="text-text-primary">{task.title}</strong>
            </p>
            <p className="text-xs text-text-muted">
              All associated comments, attachments, and timeline entries will be permanently removed.
            </p>
          </div>
        </Modal>
      )}

      {/* Create Ticket For This Task Modal */}
      {isCreateTicketModalOpen && (
        <CreateTicketModal
          isOpen={isCreateTicketModalOpen}
          onClose={() => setIsCreateTicketModalOpen(false)}
          defaultProjectId={task.project_id}
          defaultTaskId={task.id}
          onTicketCreated={(newTicket) => {
            setLinkedTickets((prev) => [newTicket, ...prev]);
            fetchActivities(1, false);
          }}
        />
      )}
    </>,
    document.body
  );
};

export default TaskDetailDrawer;
