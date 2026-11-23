import React, { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { ticketsService } from '../../../services/ticketsService';
import { RelatedTaskSelect } from './RelatedTaskSelect';
import { TASK_STATUS_LABEL } from '../../tasks/constants/taskEnums';
import { STATUS_CONFIG, SEVERITY_CONFIG } from '../constants/ticketConstants';

/**
 * RelatedTaskCard — Displays related task context on the ticket detail page.
 * - Shows task key, title, status, assignee, due date, and project.
 * - Links to the task.
 * - "No linked task" empty state with linking action for authorized users.
 * - Change and Unlink actions for authorized users.
 * - Lists other tickets belonging to the same task.
 */
export const RelatedTaskCard = ({
  ticket,
  canEdit = false,
  onTicketUpdated,
}) => {
  const [siblingTickets, setSiblingTickets] = useState([]);
  const [loadingSiblings, setLoadingSiblings] = useState(false);
  const [isPickerOpen, setIsPickerOpen] = useState(false);
  const [selectedTaskId, setSelectedTaskId] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  const task = ticket?.task;
  const taskId = task?.id || ticket?.task_id;
  const projectId = ticket?.project_id || ticket?.project?.id;

  // Fetch sibling tickets belonging to the same task
  useEffect(() => {
    if (!taskId) {
      setSiblingTickets([]);
      return;
    }

    let isMounted = true;
    setLoadingSiblings(true);

    ticketsService.getTickets({ task_id: taskId })
      .then((res) => {
        if (!isMounted) return;
        const list = Array.isArray(res) ? res : res?.items || [];
        setSiblingTickets(list.filter((t) => t.id !== ticket.id));
      })
      .catch((err) => {
        console.warn('Failed to load sibling tickets for task:', err);
        if (isMounted) setSiblingTickets([]);
      })
      .finally(() => {
        if (isMounted) setLoadingSiblings(false);
      });

    return () => {
      isMounted = false;
    };
  }, [taskId, ticket?.id]);

  const handleUnlink = async () => {
    try {
      setSaving(true);
      setError(null);
      const updated = await ticketsService.updateTicket(ticket.id, { task_id: null });
      if (onTicketUpdated) onTicketUpdated(updated);
    } catch (err) {
      console.error('Failed to unlink task:', err);
      setError(err?.response?.data?.detail || 'Failed to unlink task.');
    } finally {
      setSaving(false);
    }
  };

  const handleSaveLink = async () => {
    try {
      setSaving(true);
      setError(null);
      const updated = await ticketsService.updateTicket(ticket.id, {
        task_id: selectedTaskId ? selectedTaskId : null,
      });
      setIsPickerOpen(false);
      setSelectedTaskId('');
      if (onTicketUpdated) onTicketUpdated(updated);
    } catch (err) {
      console.error('Failed to link task:', err);
      setError(err?.response?.data?.detail || 'Failed to update related task.');
    } finally {
      setSaving(false);
    }
  };

  const getStatusBadge = (status) => {
    const s = (status || '').toLowerCase();
    const label = TASK_STATUS_LABEL[s] || status || 'Todo';
    if (s === 'done' || s === 'resolved') {
      return (
        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-semibold bg-status-resolved/10 text-status-resolved border border-status-resolved/30">
          <span className="w-1.5 h-1.5 rounded-full bg-status-resolved" />
          {label}
        </span>
      );
    }
    if (s === 'in_progress' || s === 'wip') {
      return (
        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-semibold bg-status-wip/10 text-status-wip border border-status-wip/30">
          <span className="w-1.5 h-1.5 rounded-full bg-status-wip" />
          {label}
        </span>
      );
    }
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-semibold bg-canvas-lavender text-text-secondary border border-border-subtle">
        <span className="w-1.5 h-1.5 rounded-full bg-outline" />
        {label}
      </span>
    );
  };

  const formatDueDate = (dateStr) => {
    if (!dateStr) return 'No due date';
    return new Date(dateStr).toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' });
  };

  return (
    <div className="bg-surface-card rounded-xl p-space-lg shadow-sm space-y-space-md border border-border-subtle/50">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border-subtle pb-space-sm">
        <div className="flex items-center gap-2">
          <span className="material-symbols-outlined text-primary text-xl">assignment</span>
          <h2 className="font-headline-sm text-headline-sm text-text-primary">Related Task</h2>
        </div>
        {task && canEdit && !isPickerOpen && (
          <div className="flex items-center gap-1.5">
            <button
              type="button"
              onClick={() => {
                setSelectedTaskId(task.id);
                setIsPickerOpen(true);
              }}
              disabled={saving}
              className="px-2.5 py-1 rounded-md text-xs font-semibold text-primary hover:bg-canvas-lavender transition-colors flex items-center gap-1"
              title="Change related task"
            >
              <span className="material-symbols-outlined text-xs">edit</span>
              <span>Change</span>
            </button>
            <button
              type="button"
              onClick={handleUnlink}
              disabled={saving}
              className="px-2 py-1 rounded-md text-xs font-semibold text-text-muted hover:text-severity-blocker hover:bg-error-container/30 transition-colors flex items-center gap-1"
              title="Unlink from task"
            >
              <span className="material-symbols-outlined text-xs">link_off</span>
              <span>Unlink</span>
            </button>
          </div>
        )}
      </div>

      {/* Error alert */}
      {error && (
        <div className="p-2.5 rounded-lg bg-error-container text-on-error-container font-body-sm text-xs flex items-center justify-between gap-2">
          <span>{error}</span>
          <button type="button" onClick={() => setError(null)} className="text-text-muted hover:text-text-primary">
            <span className="material-symbols-outlined text-sm">close</span>
          </button>
        </div>
      )}

      {/* Inline Task Picker Mode */}
      {isPickerOpen ? (
        <div className="p-3 rounded-xl bg-canvas-main border border-border-subtle space-y-3 animate-in fade-in">
          <p className="font-label-sm text-label-sm text-text-secondary font-semibold">
            Select a new task from this project:
          </p>
          <RelatedTaskSelect
            projectId={projectId}
            selectedTaskId={selectedTaskId}
            onChange={(newId) => setSelectedTaskId(newId)}
          />
          <div className="flex items-center justify-end gap-2 pt-1">
            <button
              type="button"
              onClick={() => {
                setIsPickerOpen(false);
                setSelectedTaskId('');
              }}
              disabled={saving}
              className="px-3 py-1.5 rounded-lg text-xs font-semibold text-text-secondary hover:bg-canvas-lavender transition-colors"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleSaveLink}
              disabled={saving}
              className="px-3.5 py-1.5 rounded-lg text-xs font-semibold bg-btn-primary hover:bg-primary-hover text-on-primary shadow-xs transition-colors flex items-center gap-1"
            >
              {saving ? (
                <>
                  <span className="material-symbols-outlined text-xs animate-spin">refresh</span>
                  <span>Saving...</span>
                </>
              ) : (
                <span>Save</span>
              )}
            </button>
          </div>
        </div>
      ) : task ? (
        /* Linked Task Active State */
        <div className="space-y-space-md font-body-sm text-body-sm">
          {/* Task Key & Title banner */}
          <div className="p-3 rounded-xl bg-canvas-lavender/50 border border-border-subtle/60 flex flex-col gap-1.5">
            <div className="flex items-center justify-between gap-2">
              <Link
                to={`/workspace/tasks?task_id=${task.id}`}
                className="font-mono font-bold text-xs text-primary hover:underline flex items-center gap-1 group"
                title="View task in All Tasks"
              >
                <span>{task.key || task.task_number || 'TASK'}</span>
                <span className="material-symbols-outlined text-xs group-hover:translate-x-0.5 transition-transform">
                  open_in_new
                </span>
              </Link>
              {getStatusBadge(task.status)}
            </div>
            <Link
              to={`/workspace/tasks?task_id=${task.id}`}
              className="font-label-md text-label-md text-text-primary hover:text-primary transition-colors font-semibold line-clamp-2"
            >
              {task.title}
            </Link>
          </div>

          {/* Key metadata grid */}
          <div className="space-y-2 text-xs divide-y divide-border-subtle/30">
            {/* Assignee */}
            <div className="flex items-center justify-between pt-1">
              <span className="text-text-secondary">Assignee</span>
              <div className="flex items-center gap-1.5">
                <span className="w-5 h-5 rounded-full bg-primary-fixed text-primary font-bold text-[10px] flex items-center justify-center">
                  {(task.assignee_name || 'UN').slice(0, 2).toUpperCase()}
                </span>
                <span className="font-medium text-text-primary">
                  {task.assignee_name || 'Unassigned'}
                </span>
              </div>
            </div>

            {/* Due date */}
            <div className="flex items-center justify-between pt-2">
              <span className="text-text-secondary">Due Date</span>
              <span className="font-mono text-text-primary">
                {formatDueDate(task.due_date)}
              </span>
            </div>

            {/* Project */}
            <div className="flex items-center justify-between pt-2">
              <span className="text-text-secondary">Project</span>
              <span className="font-semibold text-primary truncate max-w-[180px]">
                {ticket.project?.title || ticket.project?.name || 'Workspace Project'}
              </span>
            </div>
          </div>

          {/* Sibling tickets section */}
          <div className="pt-2 border-t border-border-subtle/60 space-y-2">
            <div className="flex items-center justify-between">
              <span className="font-label-sm text-xs font-semibold text-text-muted uppercase tracking-wider">
                Other Tickets of This Task
              </span>
              <span className="px-1.5 py-0.2 rounded-full text-[10px] font-bold bg-canvas-lavender text-text-secondary">
                {siblingTickets.length}
              </span>
            </div>

            {loadingSiblings ? (
              <div className="py-2 flex items-center gap-2 text-text-muted text-xs">
                <span className="material-symbols-outlined text-xs animate-spin">refresh</span>
                <span>Loading sibling tickets...</span>
              </div>
            ) : siblingTickets.length === 0 ? (
              <p className="text-xs text-text-muted py-1 italic">
                No other tickets linked to this task.
              </p>
            ) : (
              <div className="space-y-1.5 max-h-48 overflow-y-auto pr-1">
                {siblingTickets.map((st) => {
                  const sevKey = (st.severity || '').toLowerCase();
                  const sevCfg = SEVERITY_CONFIG[sevKey] || SEVERITY_CONFIG.minor;
                  const stKey = (st.status || '').toLowerCase();
                  const stCfg = STATUS_CONFIG[stKey] || STATUS_CONFIG.open;

                  return (
                    <Link
                      key={st.id}
                      to={`/workspace/tickets/${st.id}`}
                      className="p-2 rounded-lg bg-canvas-main hover:bg-canvas-lavender/60 border border-border-subtle/50 transition-colors flex items-center justify-between gap-2 group block"
                    >
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-1.5">
                          <span className="font-mono font-bold text-[11px] text-text-muted group-hover:text-primary transition-colors">
                            {st.ticket_number}
                          </span>
                          <span className={`px-1 py-0.2 rounded text-[9px] font-bold uppercase ${sevCfg.bgBadge} ${sevCfg.textBadge}`}>
                            {sevCfg.shortLabel}
                          </span>
                        </div>
                        <p className="text-xs text-text-primary truncate font-medium mt-0.5">
                          {st.title}
                        </p>
                      </div>

                      <span className={`px-1.5 py-0.2 rounded-full text-[10px] font-semibold shrink-0 ${stCfg.bg} ${stCfg.text}`}>
                        {stCfg.label}
                      </span>
                    </Link>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      ) : (
        /* Empty State: No linked task */
        <div className="py-6 px-4 rounded-xl border border-dashed border-border-subtle text-center flex flex-col items-center justify-center gap-2">
          <div className="w-10 h-10 rounded-full bg-canvas-lavender flex items-center justify-center text-text-muted">
            <span className="material-symbols-outlined text-xl">link_off</span>
          </div>
          <div className="space-y-0.5">
            <p className="font-label-md text-sm font-semibold text-text-primary">No linked task</p>
            <p className="font-body-sm text-xs text-text-muted max-w-[220px]">
              This ticket is not linked to any workspace task.
            </p>
          </div>
          {canEdit && (
            <button
              type="button"
              onClick={() => {
                setSelectedTaskId('');
                setIsPickerOpen(true);
              }}
              disabled={saving}
              className="mt-2 px-3 py-1.5 rounded-lg text-xs font-semibold bg-btn-primary hover:bg-primary-hover text-on-primary shadow-xs transition-colors flex items-center gap-1.5"
            >
              <span className="material-symbols-outlined text-xs">add_link</span>
              <span>Link a Task</span>
            </button>
          )}
        </div>
      )}
    </div>
  );
};

export default RelatedTaskCard;
