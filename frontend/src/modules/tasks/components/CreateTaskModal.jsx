import React, { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import Modal from '../../design-system/components/Modal';
import Avatar from '../../../components/common/Avatar';
import { projectsService } from '../../../services/projectsService';
import { membersService } from '../../../services/membersService';
import { ticketsService } from '../../../services/ticketsService';
import { taskService } from '../services/taskService';
import { authService } from '../../../services/api';
import {
  TASK_STATUS_OPTIONS,
  TASK_STATUS_LABEL,
  TASK_PRIORITY_OPTIONS,
  TASK_PRIORITY_LABEL,
  TASK_TYPE_OPTIONS,
  TASK_TYPE_LABEL,
  TASK_STATUS,
  TASK_PRIORITY,
  TASK_TYPE,
} from '../constants/taskEnums';

const TICKET_SEVERITY_BADGE = {
  blocker: { label: 'Blocker', bg: 'bg-severity-blocker/15 text-severity-blocker' },
  critical: { label: 'Critical', bg: 'bg-severity-critical/15 text-severity-critical' },
  major: { label: 'Major', bg: 'bg-severity-major/15 text-severity-major' },
  minor: { label: 'Minor', bg: 'bg-severity-minor/15 text-severity-minor' },
};

const TICKET_STATUS_BADGE = {
  open: { label: 'Open', bg: 'bg-primary/10 text-primary' },
  in_progress: { label: 'In Progress', bg: 'bg-warning/15 text-warning' },
  on_hold: { label: 'On Hold', bg: 'bg-canvas-lavender text-text-secondary' },
  resolved: { label: 'Resolved', bg: 'bg-success/15 text-success' },
  closed: { label: 'Closed', bg: 'bg-canvas-lavender text-text-muted' },
};

/**
 * CreateTaskModal
 * Clean, production modal for creating tasks with:
 * - Real organization employee loading for Assignee and Assigner dropdowns
 * - Initials avatar, full display name, and role/designation
 * - Loading, empty, and error states with retry
 * - Search by employee name, email, and role
 * - Project selection and Related Tickets multi-select integration
 * - Automatic clearing of incompatible tickets when project changes
 */
export const CreateTaskModal = ({
  isOpen,
  onClose,
  onCreateTask,
  defaultProjectId = '',
}) => {
  // Task Details
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [status, setStatus] = useState('');
  const [priority, setPriority] = useState('');
  const [taskType, setTaskType] = useState('');
  const [storyPoints, setStoryPoints] = useState('');
  const [dueDate, setDueDate] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState('');

  // Project state
  const [projectId, setProjectId] = useState(defaultProjectId || '');
  const [projects, setProjects] = useState([]);
  const [isLoadingProjects, setIsLoadingProjects] = useState(false);
  const [projectsError, setProjectsError] = useState(null);

  // Related Tickets state
  const [tickets, setTickets] = useState([]);
  const [isLoadingTickets, setIsLoadingTickets] = useState(false);
  const [ticketsError, setTicketsError] = useState(null);
  const [selectedTicketIds, setSelectedTicketIds] = useState([]);
  const [isTicketsOpen, setIsTicketsOpen] = useState(false);
  const [ticketSearch, setTicketSearch] = useState('');

  // Workspace members & authenticated user state
  const [users, setUsers] = useState([]);
  const [isLoadingUsers, setIsLoadingUsers] = useState(false);
  const [usersError, setUsersError] = useState(null);
  const [currentUser, setCurrentUser] = useState(() => authService.getCurrentUserSync());
  const [assignerId, setAssignerId] = useState('');
  const [assigneeId, setAssigneeId] = useState('');

  // Dropdown open states & search query states
  const [isAssignerOpen, setIsAssignerOpen] = useState(false);
  const [isAssigneeOpen, setIsAssigneeOpen] = useState(false);
  const [assignerSearch, setAssignerSearch] = useState('');
  const [assigneeSearch, setAssigneeSearch] = useState('');

  const assignerRef = useRef(null);
  const assigneeRef = useRef(null);
  const ticketsRef = useRef(null);

  // Fetch active eligible employees from existing organization-scoped API
  const fetchUsers = useCallback(async () => {
    setIsLoadingUsers(true);
    setUsersError(null);
    try {
      let data = await membersService.listMembers();
      let list = Array.isArray(data)
        ? data
        : (data?.members || data?.users || data?.items || data?.data || []);
      if (!list || list.length === 0) {
        try {
          const fallbackData = await projectsService.getUsers();
          const fallbackList = Array.isArray(fallbackData)
            ? fallbackData
            : (fallbackData?.members || fallbackData?.users || fallbackData?.items || fallbackData?.data || []);
          if (fallbackList && fallbackList.length > 0) {
            list = fallbackList;
          }
        } catch {
          // ignore fallback error
        }
      }
      // Keep eligible active employees belonging to organization
      const activeMembers = (list || []).filter(
        (m) => m && m.is_active !== false && m.account_state !== 'inactive' && m.status !== 'suspended'
      );
      setUsers(activeMembers);
    } catch (err) {
      console.error('Failed to load workspace members:', err);
      try {
        const fallbackData = await projectsService.getUsers();
        const fallbackList = Array.isArray(fallbackData)
          ? fallbackData
          : (fallbackData?.members || fallbackData?.users || fallbackData?.items || fallbackData?.data || []);
        if (fallbackList && fallbackList.length > 0) {
          const activeFallback = fallbackList.filter(
            (m) => m && m.is_active !== false && m.account_state !== 'inactive'
          );
          setUsers(activeFallback);
          return;
        }
      } catch (err2) {
        console.error('Fallback users fetch also failed:', err2);
      }
      setUsersError('Failed to load employee list. Please try again.');
    } finally {
      setIsLoadingUsers(false);
    }
  }, []);

  // Fetch available projects
  const fetchProjects = useCallback(async () => {
    setIsLoadingProjects(true);
    setProjectsError(null);
    try {
      const data = await projectsService.getProjects();
      const list = Array.isArray(data) ? data : (data?.projects || data?.items || []);
      setProjects(list || []);
    } catch (err) {
      console.error('Failed to load projects:', err);
      setProjectsError('Failed to load projects.');
    } finally {
      setIsLoadingProjects(false);
    }
  }, []);

  // Fetch tickets scoped to selected project (or all organization tickets if no project selected)
  const fetchTickets = useCallback(async (targetProjId) => {
    setIsLoadingTickets(true);
    setTicketsError(null);
    try {
      const params = targetProjId ? { project_id: targetProjId } : {};
      const data = await ticketsService.getTickets(params);
      const list = Array.isArray(data) ? data : (data?.tickets || data?.items || []);
      setTickets(list || []);
    } catch (err) {
      console.error('Failed to load tickets:', err);
      setTicketsError('Failed to load tickets.');
    } finally {
      setIsLoadingTickets(false);
    }
  }, []);

  // Reset form and initialize data when modal opens
  useEffect(() => {
    if (isOpen) {
      setTitle('');
      setDescription('');
      setStatus('');
      setPriority('');
      setTaskType('');
      setStoryPoints('');
      setDueDate('');
      setErrorMessage('');
      setIsSubmitting(false);

      const initialProj = defaultProjectId || '';
      setProjectId(initialProj);
      setSelectedTicketIds([]);
      setTicketSearch('');
      setIsTicketsOpen(false);

      // Authenticated user initialization
      const user = authService.getCurrentUserSync();
      if (user) {
        setCurrentUser(user);
        setAssignerId(user.id);
      } else {
        authService.getCurrentUser().then((u) => {
          if (u) {
            setCurrentUser(u);
            setAssignerId(u.id);
          }
        });
      }

      setAssigneeId('');
      setIsAssignerOpen(false);
      setIsAssigneeOpen(false);
      setAssignerSearch('');
      setAssigneeSearch('');

      // Fetch fresh data
      fetchUsers();
      fetchProjects();
      fetchTickets(initialProj);
    }
  }, [isOpen, defaultProjectId, fetchUsers, fetchProjects, fetchTickets]);

  // Handle project change: clear tickets that do not belong to the new project and reload
  const handleProjectChange = (newProjId) => {
    setProjectId(newProjId);
    setSelectedTicketIds((prev) => {
      if (!newProjId) return prev;
      return prev.filter((tid) => {
        const t = tickets.find((item) => item.id === tid);
        return !t || !t.project_id || t.project_id === newProjId;
      });
    });
    fetchTickets(newProjId);
  };

  // Toggle ticket selection
  const handleToggleTicket = (ticketId) => {
    setSelectedTicketIds((prev) =>
      prev.includes(ticketId) ? prev.filter((id) => id !== ticketId) : [...prev, ticketId]
    );
  };

  const handleRemoveTicket = (ticketId, e) => {
    if (e) e.stopPropagation();
    setSelectedTicketIds((prev) => prev.filter((id) => id !== ticketId));
  };

  // Handle click outside to close dropdowns
  useEffect(() => {
    const handleClickOutside = (e) => {
      if (assignerRef.current && !assignerRef.current.contains(e.target)) {
        setIsAssignerOpen(false);
      }
      if (assigneeRef.current && !assigneeRef.current.contains(e.target)) {
        setIsAssigneeOpen(false);
      }
      if (ticketsRef.current && !ticketsRef.current.contains(e.target)) {
        setIsTicketsOpen(false);
      }
    };
    if (isAssignerOpen || isAssigneeOpen || isTicketsOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [isAssignerOpen, isAssigneeOpen, isTicketsOpen]);

  // Check RBAC permission for changing assigner (owner, admin, manager, lead)
  const userRole = (currentUser?.role || '').toLowerCase();
  const canChangeAssigner = ['owner', 'admin', 'manager', 'lead'].includes(userRole);

  // Filtered users for searching
  const filteredAssigners = useMemo(() => {
    if (!assignerSearch.trim()) return users;
    const q = assignerSearch.toLowerCase().trim();
    return users.filter((u) => {
      const name = (u.full_name || u.display_name || '').toLowerCase();
      const email = (u.email || '').toLowerCase();
      const role = (u.role || u.title || '').toLowerCase();
      return name.includes(q) || email.includes(q) || role.includes(q);
    });
  }, [users, assignerSearch]);

  const filteredAssignees = useMemo(() => {
    if (!assigneeSearch.trim()) return users;
    const q = assigneeSearch.toLowerCase().trim();
    return users.filter((u) => {
      const name = (u.full_name || u.display_name || '').toLowerCase();
      const email = (u.email || '').toLowerCase();
      const role = (u.role || u.title || '').toLowerCase();
      return name.includes(q) || email.includes(q) || role.includes(q);
    });
  }, [users, assigneeSearch]);

  // Filtered tickets for searching
  const filteredTickets = useMemo(() => {
    if (!ticketSearch.trim()) return tickets;
    const q = ticketSearch.toLowerCase().trim();
    return tickets.filter((t) => {
      const num = (t.ticket_number || '').toLowerCase();
      const tit = (t.title || '').toLowerCase();
      return num.includes(q) || tit.includes(q);
    });
  }, [tickets, ticketSearch]);

  const selectedAssigner = users.find((u) => u.id === assignerId) || currentUser;
  const selectedAssignee = users.find((u) => u.id === assigneeId) || null;
  const selectedTickets = useMemo(() => {
    return tickets.filter((t) => selectedTicketIds.includes(t.id));
  }, [tickets, selectedTicketIds]);

  const hasUnsavedChanges = Boolean(
    title.trim() ||
    description.trim() ||
    status ||
    priority ||
    taskType ||
    storyPoints ||
    dueDate ||
    projectId ||
    selectedTicketIds.length > 0 ||
    assigneeId ||
    (currentUser && assignerId !== currentUser.id)
  );

  const handleSubmit = async (e) => {
    if (e && e.preventDefault) e.preventDefault();
    setErrorMessage('');

    if (!title.trim()) {
      setErrorMessage('Task title is required');
      return;
    }

    try {
      setIsSubmitting(true);
      const payload = {
        title: title.trim(),
        description: description.trim() || null,
        status: status || TASK_STATUS.TODO,
        priority: priority || TASK_PRIORITY.MEDIUM,
        task_type: taskType || TASK_TYPE.FEATURE,
        story_points: storyPoints !== '' ? Number(storyPoints) : null,
        due_date: dueDate ? new Date(dueDate).toISOString() : null,
        project_id: projectId || null,
        assignee_id: assigneeId || null,
        assigned_to_id: assigneeId || null,
        assigner_id: assignerId || currentUser?.id,
        reporter_id: assignerId || currentUser?.id,
        ticket_ids: selectedTicketIds.length > 0 ? selectedTicketIds : undefined,
      };

      const created = await onCreateTask(payload);
      const taskId = created?.id;

      // Ensure tickets are linked if backend create was called via a wrapper that omitted ticket_ids
      if (taskId && selectedTicketIds.length > 0) {
        const linkPromises = selectedTicketIds.map((tid) =>
          taskService.linkTicket(taskId, tid).catch((err) => ({ error: true, tid, err }))
        );
        const linkResults = await Promise.all(linkPromises);
        const failed = linkResults.filter((r) => r?.error);
        if (failed.length > 0 && failed.length === selectedTicketIds.length) {
          setErrorMessage(
            `Task was created, but failed to link ${failed.length} ticket(s). You can link them directly from the task details.`
          );
          return;
        }
      }

      onClose();
    } catch (err) {
      console.error('Failed to create task', err);
      const detail = err.response?.data?.detail;
      const msg = typeof detail === 'string' ? detail : detail?.message || err.message || 'Failed to create task.';
      setErrorMessage(msg);
    } finally {
      setIsSubmitting(false);
    }
  };

  const footer = (
    <div className="flex items-center justify-end gap-3 w-full">
      <button
        type="button"
        onClick={onClose}
        disabled={isSubmitting}
        className="px-4 py-2.5 rounded-xl border border-border-subtle text-text-secondary hover:bg-canvas-lavender font-label-md text-label-md transition-colors"
      >
        Cancel
      </button>
      <button
        type="button"
        onClick={handleSubmit}
        disabled={isSubmitting}
        className="px-5 py-2.5 rounded-xl bg-btn-primary hover:bg-primary-hover text-on-primary font-label-md text-label-md shadow-sm transition-all flex items-center gap-1.5 disabled:opacity-50"
      >
        {isSubmitting ? (
          <>
            <span className="material-symbols-outlined text-[16px] animate-spin">refresh</span>
            <span>Creating...</span>
          </>
        ) : (
          <>
            <span className="material-symbols-outlined text-[16px]">add</span>
            <span>Create Task</span>
          </>
        )}
      </button>
    </div>
  );

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Create Task"
      subtitle="Add an operational or feature work item"
      size="lg"
      footer={footer}
      hasUnsavedChanges={hasUnsavedChanges}
    >
      <form onSubmit={handleSubmit} className="space-y-4">
        {errorMessage && (
          <div className="p-3 rounded-xl bg-error-container/40 text-error font-body-sm flex items-center gap-2">
            <span className="material-symbols-outlined text-[18px]">error</span>
            <span>{errorMessage}</span>
          </div>
        )}

        {/* Title */}
        <div>
          <label className="block font-label-md text-label-md text-text-secondary mb-1">
            Title <span className="text-error">*</span>
          </label>
          <input
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="e.g. Implement Kafka telemetry health check"
            className="w-full h-11 px-3.5 rounded-xl border border-border-subtle bg-canvas-main text-text-primary font-body-sm placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary"
            autoFocus
          />
        </div>

        {/* Project & Related Tickets Row */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {/* Project */}
          <div>
            <div className="flex items-center justify-between mb-1">
              <label className="block font-label-md text-label-md text-text-secondary">
                Project
              </label>
              {isLoadingProjects && (
                <span className="text-[11px] text-text-muted flex items-center gap-1">
                  <span className="material-symbols-outlined text-[13px] animate-spin">refresh</span>
                  Loading...
                </span>
              )}
            </div>
            <select
              value={projectId}
              onChange={(e) => handleProjectChange(e.target.value)}
              className="w-full h-11 px-3 rounded-lg border border-border-subtle bg-canvas-main text-text-primary font-body-sm focus:outline-none focus:ring-2 focus:ring-primary/20 cursor-pointer"
            >
              <option value="">No Project (Organization Task)</option>
              {projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} ({p.key})
                </option>
              ))}
            </select>
            {projectsError && (
              <p className="text-xs text-error mt-1">{projectsError}</p>
            )}
          </div>

          {/* Related Tickets */}
          <div className="relative" ref={ticketsRef}>
            <div className="flex items-center justify-between mb-1">
              <label className="block font-label-md text-label-md text-text-secondary">
                Related Tickets
              </label>
              <span className="text-[11px] text-text-muted">Optional</span>
            </div>

            <button
              type="button"
              onClick={() => {
                setIsTicketsOpen((prev) => !prev);
                setIsAssignerOpen(false);
                setIsAssigneeOpen(false);
              }}
              className="w-full min-h-[44px] px-3 py-1.5 rounded-lg border border-border-subtle bg-canvas-main hover:bg-canvas-lavender/30 flex items-center justify-between transition-colors text-left cursor-pointer focus:outline-none focus:ring-2 focus:ring-primary/20"
            >
              <div className="flex flex-wrap items-center gap-1.5 min-w-0 pr-2">
                {selectedTickets.length > 0 ? (
                  selectedTickets.map((t) => (
                    <span
                      key={t.id}
                      className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-canvas-lavender border border-border-subtle text-[11px] font-mono text-primary font-semibold"
                    >
                      <span>{t.ticket_number || t.id.slice(0, 8)}</span>
                      <span
                        onClick={(e) => handleRemoveTicket(t.id, e)}
                        className="material-symbols-outlined text-[13px] text-text-muted hover:text-error cursor-pointer"
                        title="Remove link"
                      >
                        close
                      </span>
                    </span>
                  ))
                ) : (
                  <span className="font-body-sm text-body-sm text-text-muted">
                    {projectId ? 'Select related tickets...' : 'Select related tickets (optional)...'}
                  </span>
                )}
              </div>
              <span className="material-symbols-outlined text-text-muted text-[18px] shrink-0">
                {isTicketsOpen ? 'expand_less' : 'expand_more'}
              </span>
            </button>

            {/* Related Tickets Dropdown Popover */}
            {isTicketsOpen && (
              <div className="absolute left-0 right-0 top-full mt-1.5 z-40 bg-surface-card rounded-xl shadow-xl border border-border-subtle overflow-hidden animate-in fade-in zoom-in-95 duration-100">
                <div className="p-2 border-b border-border-subtle bg-canvas-main">
                  <div className="flex items-center gap-1.5 px-2.5 py-1.5 bg-surface-card rounded-lg border border-border-subtle">
                    <span className="material-symbols-outlined text-text-muted text-[16px]">search</span>
                    <input
                      type="text"
                      value={ticketSearch}
                      onChange={(e) => setTicketSearch(e.target.value)}
                      placeholder="Search tickets by key or title..."
                      autoFocus
                      className="w-full bg-transparent border-0 outline-none font-body-sm text-body-sm text-text-primary placeholder:text-text-muted"
                    />
                    {ticketSearch && (
                      <button
                        type="button"
                        onClick={() => setTicketSearch('')}
                        className="text-text-muted hover:text-text-primary"
                      >
                        <span className="material-symbols-outlined text-[14px]">close</span>
                      </button>
                    )}
                  </div>
                </div>

                <div className="max-h-56 overflow-y-auto p-1 divide-y divide-border-subtle/30">
                  {/* Loading State */}
                  {isLoadingTickets && (
                    <div className="p-4 text-center text-text-muted font-body-sm flex items-center justify-center gap-2">
                      <span className="material-symbols-outlined text-[18px] animate-spin">refresh</span>
                      <span>Loading tickets...</span>
                    </div>
                  )}

                  {/* Error State */}
                  {!isLoadingTickets && ticketsError && (
                    <div className="p-3 text-center text-error font-body-sm space-y-2">
                      <p className="text-xs">{ticketsError}</p>
                      <button
                        type="button"
                        onClick={() => fetchTickets(projectId)}
                        className="px-2.5 py-1 text-xs rounded-lg bg-error-container/40 text-error hover:bg-error-container font-semibold transition-colors"
                      >
                        Retry
                      </button>
                    </div>
                  )}

                  {/* Empty State */}
                  {!isLoadingTickets && !ticketsError && filteredTickets.length === 0 && (
                    <div className="p-4 text-center text-text-muted font-body-sm">
                      <span className="material-symbols-outlined text-[24px] text-text-muted block mb-1">
                        confirmation_number
                      </span>
                      <span>
                        {projectId
                          ? 'No tickets found in this project'
                          : 'No tickets found for this organization'}
                      </span>
                    </div>
                  )}

                  {/* Ticket Items List */}
                  {!isLoadingTickets &&
                    !ticketsError &&
                    filteredTickets.map((t) => {
                      const isSelected = selectedTicketIds.includes(t.id);
                      const sevKey = (t.severity || '').toLowerCase();
                      const stKey = (t.status || '').toLowerCase();
                      const sevBadge = TICKET_SEVERITY_BADGE[sevKey] || { label: t.severity, bg: 'bg-canvas-lavender text-text-secondary' };
                      const stBadge = TICKET_STATUS_BADGE[stKey] || { label: t.status, bg: 'bg-canvas-lavender text-text-secondary' };

                      return (
                        <div
                          key={t.id}
                          onClick={() => handleToggleTicket(t.id)}
                          className={`w-full px-2.5 py-2 rounded-lg flex items-center justify-between cursor-pointer transition-colors ${
                            isSelected
                              ? 'bg-canvas-lavender text-primary font-semibold'
                              : 'hover:bg-canvas-lavender/50 text-text-primary'
                          }`}
                        >
                          <div className="flex items-center gap-2.5 min-w-0 pr-2">
                            <span
                              className={`w-4 h-4 rounded border flex items-center justify-center shrink-0 transition-colors ${
                                isSelected ? 'bg-primary border-primary text-on-primary' : 'border-border-subtle bg-surface-card'
                              }`}
                            >
                              {isSelected && (
                                <span className="material-symbols-outlined text-[13px] text-white">check</span>
                              )}
                            </span>

                            <span className="font-mono text-label-sm font-bold text-primary bg-primary-fixed px-1.5 py-0.5 rounded shrink-0">
                              {t.ticket_number || `TKT-${t.id.slice(0, 4)}`}
                            </span>

                            <div className="min-w-0">
                              <p className="font-body-sm text-body-sm text-text-primary truncate" title={t.title}>
                                {t.title}
                              </p>
                              <div className="flex items-center gap-1.5 mt-0.5">
                                <span className={`text-[10px] font-semibold px-1.5 py-0.2 rounded ${stBadge.bg}`}>
                                  {stBadge.label}
                                </span>
                                {t.severity && (
                                  <span className={`text-[10px] font-semibold px-1.5 py-0.2 rounded ${sevBadge.bg}`}>
                                    {sevBadge.label}
                                  </span>
                                )}
                              </div>
                            </div>
                          </div>
                        </div>
                      );
                    })}
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Type, Priority, Status */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          {/* Task Type */}
          <div>
            <label className="block font-label-md text-label-md text-text-secondary mb-1">
              Type
            </label>
            <select
              value={taskType}
              onChange={(e) => setTaskType(e.target.value)}
              className="w-full h-10 px-3 rounded-lg border border-border-subtle bg-canvas-main text-text-primary font-body-sm focus:outline-none focus:ring-2 focus:ring-primary/20"
            >
              <option value="">Select type</option>
              {TASK_TYPE_OPTIONS.map((tp) => (
                <option key={tp} value={tp}>
                  {TASK_TYPE_LABEL[tp]}
                </option>
              ))}
            </select>
          </div>

          {/* Priority */}
          <div>
            <label className="block font-label-md text-label-md text-text-secondary mb-1">
              Priority
            </label>
            <select
              value={priority}
              onChange={(e) => setPriority(e.target.value)}
              className="w-full h-10 px-3 rounded-lg border border-border-subtle bg-canvas-main text-text-primary font-body-sm focus:outline-none focus:ring-2 focus:ring-primary/20"
            >
              <option value="">Select priority</option>
              {TASK_PRIORITY_OPTIONS.map((pr) => (
                <option key={pr} value={pr}>
                  {TASK_PRIORITY_LABEL[pr]}
                </option>
              ))}
            </select>
          </div>

          {/* Status */}
          <div>
            <label className="block font-label-md text-label-md text-text-secondary mb-1">
              Status
            </label>
            <select
              value={status}
              onChange={(e) => setStatus(e.target.value)}
              className="w-full h-10 px-3 rounded-lg border border-border-subtle bg-canvas-main text-text-primary font-body-sm focus:outline-none focus:ring-2 focus:ring-primary/20"
            >
              <option value="">Select status</option>
              {TASK_STATUS_OPTIONS.map((st) => (
                <option key={st} value={st}>
                  {TASK_STATUS_LABEL[st]}
                </option>
              ))}
            </select>
          </div>
        </div>

        {/* Story Points & Due Date */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div>
            <label className="block font-label-md text-label-md text-text-secondary mb-1">
              Story Points
            </label>
            <input
              type="number"
              min="0"
              max="100"
              value={storyPoints}
              onChange={(e) => setStoryPoints(e.target.value)}
              placeholder="0"
              className="w-full h-10 px-3 rounded-lg border border-border-subtle bg-canvas-main text-text-primary font-body-sm focus:outline-none focus:ring-2 focus:ring-primary/20"
            />
          </div>
          <div>
            <label className="block font-label-md text-label-md text-text-secondary mb-1">
              Due Date
            </label>
            <input
              type="date"
              value={dueDate}
              onChange={(e) => setDueDate(e.target.value)}
              className="w-full h-10 px-3 rounded-lg border border-border-subtle bg-canvas-main text-text-primary font-body-sm focus:outline-none focus:ring-2 focus:ring-primary/20"
            />
          </div>
        </div>

        {/* Assigner & Assignee Fields */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {/* Assigner */}
          <div className="relative" ref={assignerRef}>
            <div className="flex items-center justify-between mb-1">
              <label className="block font-label-md text-label-md text-text-secondary">
                Assigner
              </label>
              {!canChangeAssigner && (
                <span className="text-[11px] text-text-muted flex items-center gap-0.5" title="Only administrators, managers, and leads can change assigner">
                  <span className="material-symbols-outlined text-[13px]">lock</span>
                  Locked
                </span>
              )}
            </div>

            <button
              type="button"
              disabled={!canChangeAssigner || isLoadingUsers}
              onClick={() => {
                setIsAssignerOpen((prev) => !prev);
                setIsAssigneeOpen(false);
                setIsTicketsOpen(false);
              }}
              className={`w-full h-11 px-3 rounded-lg border border-border-subtle bg-canvas-main flex items-center justify-between transition-colors text-left ${
                canChangeAssigner ? 'hover:bg-canvas-lavender/30 cursor-pointer focus:outline-none focus:ring-2 focus:ring-primary/20' : 'cursor-default opacity-85'
              }`}
            >
              <div className="flex items-center gap-2 min-w-0 pr-2">
                <Avatar
                  src={selectedAssigner?.avatar_url}
                  name={selectedAssigner?.display_name || selectedAssigner?.full_name}
                  size="sm"
                  shape="rounded-full"
                />
                <div className="flex flex-col min-w-0">
                  <span className="font-label-md text-label-md text-text-primary truncate">
                    {selectedAssigner?.display_name || selectedAssigner?.full_name || 'Select Assigner'}
                  </span>
                  {(selectedAssigner?.role || selectedAssigner?.title) && (
                    <span className="text-[10px] text-text-muted capitalize leading-none">
                      {selectedAssigner.title || selectedAssigner.role}
                    </span>
                  )}
                </div>
              </div>
              {canChangeAssigner ? (
                <span className="material-symbols-outlined text-text-muted text-[18px]">
                  {isAssignerOpen ? 'expand_less' : 'expand_more'}
                </span>
              ) : (
                <span className="material-symbols-outlined text-text-muted text-[16px]">lock</span>
              )}
            </button>

            {/* Assigner Dropdown Popover */}
            {isAssignerOpen && canChangeAssigner && (
              <div className="absolute left-0 right-0 top-full mt-1.5 z-40 bg-surface-card rounded-xl shadow-xl border border-border-subtle overflow-hidden animate-in fade-in zoom-in-95 duration-100">
                <div className="p-2 border-b border-border-subtle bg-canvas-main">
                  <div className="flex items-center gap-1.5 px-2.5 py-1.5 bg-surface-card rounded-lg border border-border-subtle">
                    <span className="material-symbols-outlined text-text-muted text-[16px]">search</span>
                    <input
                      type="text"
                      value={assignerSearch}
                      onChange={(e) => setAssignerSearch(e.target.value)}
                      placeholder="Search assigner..."
                      autoFocus
                      className="w-full bg-transparent border-0 outline-none font-body-sm text-body-sm text-text-primary placeholder:text-text-muted"
                    />
                    {assignerSearch && (
                      <button
                        type="button"
                        onClick={() => setAssignerSearch('')}
                        className="text-text-muted hover:text-text-primary"
                      >
                        <span className="material-symbols-outlined text-[14px]">close</span>
                      </button>
                    )}
                  </div>
                </div>

                <div className="max-h-48 overflow-y-auto p-1 divide-y divide-border-subtle/30">
                  {isLoadingUsers && (
                    <div className="p-4 text-center text-text-muted font-body-sm flex items-center justify-center gap-2">
                      <span className="material-symbols-outlined text-[18px] animate-spin">refresh</span>
                      <span>Loading employees...</span>
                    </div>
                  )}

                  {!isLoadingUsers && filteredAssigners.length === 0 ? (
                    <div className="p-3 text-center text-text-muted font-body-sm text-body-sm">
                      No members found
                    </div>
                  ) : (
                    !isLoadingUsers &&
                    filteredAssigners.map((u) => {
                      const isSelected = u.id === assignerId;
                      const displayName = u.display_name || u.full_name || u.email;
                      const roleTag = u.title || u.role || 'Member';
                      return (
                        <div
                          key={u.id}
                          onClick={() => {
                            setAssignerId(u.id);
                            setIsAssignerOpen(false);
                            setAssignerSearch('');
                          }}
                          className={`w-full px-2.5 py-2 rounded-lg flex items-center justify-between cursor-pointer transition-colors ${
                            isSelected
                              ? 'bg-canvas-lavender text-primary font-semibold'
                              : 'hover:bg-canvas-lavender/50 text-text-primary'
                          }`}
                        >
                          <div className="flex items-center gap-2.5 min-w-0 pr-2">
                            <Avatar
                              src={u.avatar_url}
                              name={displayName}
                              size="sm"
                              shape="rounded-full"
                            />
                            <div className="flex flex-col text-left min-w-0">
                              <span className="font-label-md text-label-md truncate">
                                {displayName}
                              </span>
                              <span className="text-[11px] text-text-muted truncate">
                                {roleTag} • {u.email}
                              </span>
                            </div>
                          </div>
                          {isSelected && (
                            <span className="material-symbols-outlined text-primary text-[18px]">check</span>
                          )}
                        </div>
                      );
                    })
                  )}
                </div>
              </div>
            )}
          </div>

          {/* Assignee */}
          <div className="relative" ref={assigneeRef}>
            <div className="flex items-center justify-between mb-1">
              <label className="block font-label-md text-label-md text-text-secondary">
                Assignee
              </label>
              {isLoadingUsers && (
                <span className="text-[11px] text-text-muted flex items-center gap-1">
                  <span className="material-symbols-outlined text-[13px] animate-spin">refresh</span>
                  Loading...
                </span>
              )}
            </div>

            <button
              type="button"
              onClick={() => {
                setIsAssigneeOpen((prev) => !prev);
                setIsAssignerOpen(false);
                setIsTicketsOpen(false);
              }}
              className="w-full h-11 px-3 rounded-lg border border-border-subtle bg-canvas-main hover:bg-canvas-lavender/30 flex items-center justify-between transition-colors text-left cursor-pointer focus:outline-none focus:ring-2 focus:ring-primary/20"
            >
              <div className="flex items-center gap-2 min-w-0 pr-2">
                {selectedAssignee ? (
                  <>
                    <Avatar
                      src={selectedAssignee.avatar_url}
                      name={selectedAssignee.display_name || selectedAssignee.full_name}
                      size="sm"
                      shape="rounded-full"
                    />
                    <div className="flex flex-col min-w-0">
                      <span className="font-label-md text-label-md text-text-primary truncate">
                        {selectedAssignee.display_name || selectedAssignee.full_name}
                      </span>
                      {(selectedAssignee.title || selectedAssignee.role) && (
                        <span className="text-[10px] text-text-muted capitalize leading-none">
                          {selectedAssignee.title || selectedAssignee.role}
                        </span>
                      )}
                    </div>
                  </>
                ) : (
                  <div className="flex items-center gap-2 text-text-muted">
                    <span className="w-7 h-7 rounded-full bg-border-subtle/50 flex items-center justify-center text-text-muted">
                      <span className="material-symbols-outlined text-[16px]">person</span>
                    </span>
                    <span className="font-body-sm text-body-sm text-text-muted">
                      Unassigned
                    </span>
                  </div>
                )}
              </div>
              <span className="material-symbols-outlined text-text-muted text-[18px]">
                {isAssigneeOpen ? 'expand_less' : 'expand_more'}
              </span>
            </button>

            {/* Assignee Dropdown Popover */}
            {isAssigneeOpen && (
              <div className="absolute left-0 right-0 top-full mt-1.5 z-40 bg-surface-card rounded-xl shadow-xl border border-border-subtle overflow-hidden animate-in fade-in zoom-in-95 duration-100">
                <div className="p-2 border-b border-border-subtle bg-canvas-main">
                  <div className="flex items-center gap-1.5 px-2.5 py-1.5 bg-surface-card rounded-lg border border-border-subtle">
                    <span className="material-symbols-outlined text-text-muted text-[16px]">search</span>
                    <input
                      type="text"
                      value={assigneeSearch}
                      onChange={(e) => setAssigneeSearch(e.target.value)}
                      placeholder="Search employee by name or email..."
                      autoFocus
                      className="w-full bg-transparent border-0 outline-none font-body-sm text-body-sm text-text-primary placeholder:text-text-muted"
                    />
                    {assigneeSearch && (
                      <button
                        type="button"
                        onClick={() => setAssigneeSearch('')}
                        className="text-text-muted hover:text-text-primary"
                      >
                        <span className="material-symbols-outlined text-[14px]">close</span>
                      </button>
                    )}
                  </div>
                </div>

                <div className="max-h-52 overflow-y-auto p-1 divide-y divide-border-subtle/30">
                  {/* Loading State */}
                  {isLoadingUsers && (
                    <div className="p-4 text-center text-text-muted font-body-sm flex items-center justify-center gap-2">
                      <span className="material-symbols-outlined text-[18px] animate-spin">refresh</span>
                      <span>Loading employees...</span>
                    </div>
                  )}

                  {/* Error State */}
                  {!isLoadingUsers && usersError && (
                    <div className="p-3 text-center text-error font-body-sm space-y-2">
                      <p className="text-xs">{usersError}</p>
                      <button
                        type="button"
                        onClick={fetchUsers}
                        className="px-2.5 py-1 text-xs rounded-lg bg-error-container/40 text-error hover:bg-error-container font-semibold transition-colors"
                      >
                        Retry
                      </button>
                    </div>
                  )}

                  {/* Unassigned Option */}
                  {!isLoadingUsers && !usersError && (
                    <div
                      onClick={() => {
                        setAssigneeId('');
                        setIsAssigneeOpen(false);
                        setAssigneeSearch('');
                      }}
                      className={`w-full px-2.5 py-2 rounded-lg flex items-center justify-between cursor-pointer transition-colors ${
                        !assigneeId
                          ? 'bg-canvas-lavender text-primary font-semibold'
                          : 'hover:bg-canvas-lavender/50 text-text-primary'
                      }`}
                    >
                      <div className="flex items-center gap-2.5">
                        <span className="w-7 h-7 rounded-full bg-border-subtle/40 flex items-center justify-center text-text-muted">
                          <span className="material-symbols-outlined text-[16px]">person_off</span>
                        </span>
                        <div className="flex flex-col text-left">
                          <span className="font-label-md text-label-md">Unassigned</span>
                          <span className="text-[10px] text-text-muted">Leave task without an owner</span>
                        </div>
                      </div>
                      {!assigneeId && (
                        <span className="material-symbols-outlined text-primary text-[18px]">check</span>
                      )}
                    </div>
                  )}

                  {/* Empty State */}
                  {!isLoadingUsers && !usersError && filteredAssignees.length === 0 && (
                    <div className="p-4 text-center text-text-muted font-body-sm">
                      <span className="material-symbols-outlined text-[20px] text-text-muted block mb-1">
                        person_search
                      </span>
                      <span>No matching employees found</span>
                    </div>
                  )}

                  {/* Employee Items */}
                  {!isLoadingUsers &&
                    !usersError &&
                    filteredAssignees.map((u) => {
                      const isSelected = u.id === assigneeId;
                      const displayName = u.display_name || u.full_name || u.email;
                      const roleTag = u.title || u.role || 'Member';
                      return (
                        <div
                          key={u.id}
                          onClick={() => {
                            setAssigneeId(u.id);
                            setIsAssigneeOpen(false);
                            setAssigneeSearch('');
                          }}
                          className={`w-full px-2.5 py-2 rounded-lg flex items-center justify-between cursor-pointer transition-colors ${
                            isSelected
                              ? 'bg-canvas-lavender text-primary font-semibold'
                              : 'hover:bg-canvas-lavender/50 text-text-primary'
                          }`}
                        >
                          <div className="flex items-center gap-2.5 min-w-0 pr-2">
                            <Avatar
                              src={u.avatar_url}
                              name={displayName}
                              size="sm"
                              shape="rounded-full"
                            />
                            <div className="flex flex-col text-left min-w-0">
                              <span className="font-label-md text-label-md truncate">
                                {displayName}
                              </span>
                              <span className="text-[11px] text-text-muted truncate">
                                {roleTag} • {u.email}
                              </span>
                            </div>
                          </div>
                          {isSelected && (
                            <span className="material-symbols-outlined text-primary text-[18px]">check</span>
                          )}
                        </div>
                      );
                    })}
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Description */}
        <div>
          <label className="block font-label-md text-label-md text-text-secondary mb-1">
            Description
          </label>
          <textarea
            rows={3}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Add details, prerequisites, or implementation plan..."
            className="w-full p-3 rounded-xl border border-border-subtle bg-canvas-main text-text-primary font-body-sm placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-primary/20 resize-none"
          />
        </div>
      </form>
    </Modal>
  );
};

export default CreateTaskModal;
