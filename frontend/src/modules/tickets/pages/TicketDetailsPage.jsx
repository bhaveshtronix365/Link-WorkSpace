import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { ticketsService, createTicketsWebSocket } from '../../../services/ticketsService';
import { authService, resolveApiUrl } from '../../../services/api';
import { STATUS_CONFIG, SEVERITY_CONFIG, TICKET_STATUSES, TICKET_SEVERITIES } from '../constants/ticketConstants';
import { TicketCommentBox } from '../components/TicketCommentBox';
import { TicketCommentFeed } from '../components/TicketCommentFeed';
import { TicketAttachmentsCard } from '../components/TicketAttachmentsCard';
import { TicketHandoffTimeline } from '../components/TicketHandoffTimeline';
import { PassOrReassignModal } from '../components/PassOrReassignModal';
import { HoldTicketModal } from '../components/HoldTicketModal';
import { CloseTicketModal } from '../components/CloseTicketModal';
import { ReopenTicketModal } from '../components/ReopenTicketModal';
import { RelatedTaskCard } from '../components/RelatedTaskCard';

export const TicketDetailsPage = () => {
  const { id } = useParams();
  const navigate = useNavigate();

  const [ticket, setTicket] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // Status & Severity menu toggles
  const [isActionMenuOpen, setIsActionMenuOpen] = useState(false);
  const [isSeverityMenuOpen, setIsSeverityMenuOpen] = useState(false);
  const [isEditingTitle, setIsEditingTitle] = useState(false);
  const [titleInput, setTitleInput] = useState('');

  // Modals state
  const [isPassModalOpen, setIsPassModalOpen] = useState(false);
  const [isReassignModalOpen, setIsReassignModalOpen] = useState(false);
  const [isHoldModalOpen, setIsHoldModalOpen] = useState(false);
  const [isCloseModalOpen, setIsCloseModalOpen] = useState(false);
  const [isVerifyCloseModalOpen, setIsVerifyCloseModalOpen] = useState(false);
  const [isReopenModalOpen, setIsReopenModalOpen] = useState(false);
  const [actionError, setActionError] = useState(null);
  const [isAccepting, setIsAccepting] = useState(false);

  const actionMenuRef = useRef(null);
  const severityMenuRef = useRef(null);
  const isAcceptingRef = useRef(false);

  // Fetch Ticket Data
  const fetchTicket = useCallback(async (isInitial = false) => {
    try {
      if (isInitial) {
        setLoading(true);
      }
      setError(null);
      const data = await ticketsService.getTicket(id);
      setTicket(data);
      setTitleInput(data.title || '');
    } catch (err) {
      console.error('Failed to fetch ticket:', err);
      setError('Ticket not found or unable to load details.');
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    fetchTicket(true);
  }, [fetchTicket]);

  // Close menus on outside click or Escape
  useEffect(() => {
    const handleClickOutside = (e) => {
      if (actionMenuRef.current && !actionMenuRef.current.contains(e.target)) {
        setIsActionMenuOpen(false);
      }
      if (severityMenuRef.current && !severityMenuRef.current.contains(e.target)) {
        setIsSeverityMenuOpen(false);
      }
    };
    const handleKeyDown = (e) => {
      if (e.key === 'Escape') {
        setIsActionMenuOpen(false);
        setIsSeverityMenuOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, []);

  // WebSocket Live Updates
  useEffect(() => {
    const wsFn = createTicketsWebSocket || ticketsService?.createTicketsWebSocket;
    let cleanup = null;
    if (typeof wsFn === 'function') {
      const user = authService.getCurrentUserSync();
      const orgId = user?.organization_id || localStorage.getItem('organization_id') || localStorage.getItem('active_workspace_id') || '';
      cleanup = wsFn(orgId, (event) => {
        const eventType = event?.type || event?.event;
        const eventTicketId =
          event?.ticket_id ||
          event?.ticket?.id ||
          event?.data?.ticket_id ||
          event?.data?.ticket?.id ||
          event?.data?.id;
        const relevantEvents = [
          'ticket_updated',
          'ticket_status_changed',
          'ticket_attachment_changed',
          'attachment_added',
          'attachment_deleted',
          'ticket_handoff',
          'ticket_comment_added',
          'comment_added',
        ];
        if (relevantEvents.includes(eventType) && eventTicketId === id) {
          if (event?.data?.ticket && event.data.ticket.id === id) {
            setTicket(event.data.ticket);
          }
          fetchTicket();
        }
      });
    }
    return () => {
      if (typeof cleanup === 'function') cleanup();
      else if (cleanup && typeof cleanup.close === 'function') cleanup.close();
    };
  }, [id, fetchTicket]);

  // Action: Accept Ticket
  const handleAcceptTicket = async () => {
    if (isAcceptingRef.current) return;
    isAcceptingRef.current = true;
    setIsAccepting(true);
    setIsActionMenuOpen(false);
    setActionError(null);
    try {
      let currentVersion = ticket?.version;
      if (currentVersion === undefined || currentVersion === null) {
        const fresh = await ticketsService.getTicket(id);
        if (fresh) {
          setTicket(fresh);
          currentVersion = fresh.version ?? 0;
        } else {
          currentVersion = 0;
        }
      }
      const updated = await ticketsService.acceptTicket(id, { version: currentVersion });
      setTicket(updated);
      await fetchTicket();
    } catch (err) {
      console.error('Accept ticket error:', err);
      const errMsg =
        err.response?.data?.error?.message ||
        err.response?.data?.detail?.message ||
        err.response?.data?.detail ||
        err.message ||
        'Failed to accept ticket.';
      setActionError(errMsg);
      await fetchTicket();
    } finally {
      isAcceptingRef.current = false;
      setIsAccepting(false);
    }
  };

  // Action: Resume Ticket
  const handleResumeTicket = async () => {
    setIsActionMenuOpen(false);
    setActionError(null);
    try {
      const currentVersion = ticket?.version ?? 0;
      const updated = await ticketsService.resumeTicket(id, { version: currentVersion });
      setTicket(updated);
      fetchTicket();
    } catch (err) {
      console.error('Resume ticket error:', err);
      const errMsg =
        err.response?.data?.error?.message ||
        err.response?.data?.detail?.message ||
        err.response?.data?.detail ||
        err.message ||
        'Failed to resume ticket.';
      setActionError(errMsg);
      fetchTicket();
    }
  };

  // Update Severity
  const handleSeverityChange = async (newSeverity) => {
    setIsSeverityMenuOpen(false);
    if (!ticket || ticket.severity === newSeverity) return;
    try {
      setTicket(prev => ({ ...prev, severity: newSeverity }));
      await ticketsService.updateTicket(id, { severity: newSeverity });
      fetchTicket();
    } catch (err) {
      console.error('Failed to update severity:', err);
      fetchTicket();
    }
  };

  // Update Title
  const handleSaveTitle = async () => {
    if (!titleInput.trim() || titleInput === ticket.title) {
      setIsEditingTitle(false);
      return;
    }
    try {
      setTicket(prev => ({ ...prev, title: titleInput.trim() }));
      setIsEditingTitle(false);
      await ticketsService.updateTicket(id, { title: titleInput.trim() });
      fetchTicket();
    } catch (err) {
      console.error('Failed to update title:', err);
      fetchTicket();
    }
  };

  // Check SLA Status
  const isBreached = useMemo(() => {
    if (!ticket) return false;
    if (typeof ticket.is_breached === 'boolean') return ticket.is_breached;
    if (['resolved', 'closed'].includes(ticket.status)) return false;
    if (!ticket.deadline) return false;
    return new Date(ticket.deadline) < new Date();
  }, [ticket]);

  // Current user context
  const currentUser = authService.getCurrentUserSync();
  const currentUserId = currentUser?.id;
  const isOwner =
    currentUser?.role?.toLowerCase() === 'owner' ||
    currentUser?.is_owner === true ||
    ticket?.project?.organization?.owner_id === currentUserId;

  const currentStatus = (ticket?.status || 'open').toLowerCase();
  const isAssignee = ticket?.assignee_id === currentUserId;
  const isUnassigned = !ticket?.assignee_id;
  const canEditTicket = Boolean(
    currentUser && (
      isOwner ||
      currentUser?.role?.toLowerCase() === 'admin' ||
      isAssignee ||
      ticket?.reporter_id === currentUserId
    )
  );

  // Latest handoff determination
  const latestHandoff = useMemo(() => {
    if (!ticket?.handoffs || ticket.handoffs.length === 0) return null;
    return [...ticket.handoffs].sort((a, b) => new Date(b.created_at) - new Date(a.created_at))[0];
  }, [ticket?.handoffs]);

  const intendedRecipientId = latestHandoff ? latestHandoff.to_user_id : ticket?.assignee_id;
  const handoffSenderId = latestHandoff ? latestHandoff.from_user_id : null;
  const isHandoffRecipient = Boolean(currentUserId && intendedRecipientId && currentUserId === intendedRecipientId);
  const isHandoffSender = Boolean(currentUserId && handoffSenderId && currentUserId === handoffSenderId);

  // -------------------------------------------------------------------------
  // Lifecycle Rules & Tooltip Computations (Backend Alignment)
  // -------------------------------------------------------------------------

  // 1. Accept ticket (open -> in_progress OR passed -> in_progress)
  let acceptEnabled = false;
  let acceptDisabledReason = '';
  if (currentStatus === 'open') {
    if (isUnassigned || isAssignee) {
      acceptEnabled = true;
    } else {
      acceptDisabledReason = `Assigned to ${ticket?.assignee?.full_name || 'another member'}. Only the assigned user or unassigned tickets can be accepted.`;
    }
  } else if (currentStatus === 'passed') {
    if (isHandoffRecipient) {
      acceptEnabled = true;
    } else if (isHandoffSender) {
      acceptDisabledReason = 'You passed this ticket. Only the recipient can accept it.';
    } else {
      acceptDisabledReason = `Passed to ${ticket?.assignee?.full_name || 'another colleague'}. Only the recipient can accept this ticket.`;
    }
  } else if (currentStatus === 'in_progress' || currentStatus === 'wip') {
    acceptDisabledReason = 'Ticket is already in progress.';
  } else if (currentStatus === 'on_hold') {
    acceptDisabledReason = 'Ticket is currently on hold. Resume work to proceed.';
  } else if (currentStatus === 'closed') {
    acceptDisabledReason = 'Closed tickets cannot be accepted.';
  } else if (currentStatus === 'resolved') {
    acceptDisabledReason = 'Resolved tickets cannot be accepted.';
  }

  // 2. Pass to colleague (in_progress -> passed, only assignee)
  let passEnabled = false;
  let passDisabledReason = '';
  if (currentStatus === 'in_progress' || currentStatus === 'wip') {
    if (isAssignee) {
      passEnabled = true;
    } else {
      passDisabledReason = 'Only the current assignee can pass this ticket to a colleague.';
    }
  } else if (currentStatus === 'open') {
    passDisabledReason = "You can't pass an open ticket. Accept it into in-progress first.";
  } else if (currentStatus === 'passed') {
    passDisabledReason = 'Ticket is already passed to a colleague. The recipient must accept it first.';
  } else if (currentStatus === 'on_hold') {
    passDisabledReason = 'Resume this ticket from hold before passing it to a colleague.';
  } else if (currentStatus === 'closed') {
    passDisabledReason = 'Cannot pass a closed ticket.';
  } else if (currentStatus === 'resolved') {
    passDisabledReason = 'Cannot pass a resolved ticket.';
  }

  // 3. Put on hold (in_progress -> on_hold, only assignee)
  let holdEnabled = false;
  let holdDisabledReason = '';
  if (currentStatus === 'in_progress' || currentStatus === 'wip') {
    if (isAssignee) {
      holdEnabled = true;
    } else {
      holdDisabledReason = 'Only the current assignee can place this ticket on hold.';
    }
  } else if (currentStatus === 'on_hold') {
    holdDisabledReason = 'Ticket is already on hold.';
  } else if (currentStatus === 'open') {
    holdDisabledReason = 'Only in-progress tickets can be placed on hold.';
  } else if (currentStatus === 'passed') {
    holdDisabledReason = 'Passed tickets must be accepted before placing on hold.';
  } else if (currentStatus === 'closed') {
    holdDisabledReason = 'Cannot place a closed ticket on hold.';
  } else if (currentStatus === 'resolved') {
    holdDisabledReason = 'Cannot place a resolved ticket on hold.';
  }

  // 4. Resume (on_hold -> in_progress: only assignee)
  let resumeEnabled = false;
  let resumeDisabledReason = '';
  if (currentStatus === 'on_hold') {
    if (isAssignee) {
      resumeEnabled = true;
    } else {
      resumeDisabledReason = 'Only the assigned user can resume work from on hold.';
    }
  } else if (currentStatus === 'passed') {
    resumeDisabledReason = 'Passed tickets must be accepted by the recipient, not resumed.';
  } else if (currentStatus === 'in_progress' || currentStatus === 'wip') {
    resumeDisabledReason = 'Ticket is already in progress.';
  } else if (currentStatus === 'open') {
    resumeDisabledReason = 'Open tickets must be accepted, not resumed.';
  } else if (currentStatus === 'closed') {
    resumeDisabledReason = 'Closed tickets cannot be resumed.';
  } else if (currentStatus === 'resolved') {
    resumeDisabledReason = 'Resolved tickets cannot be resumed.';
  }

  // 5. Verify & close (Owner only, shown only when passed to owner or held by owner in progress)
  const isVerifyCloseEligible =
    isOwner &&
    ((currentStatus === 'passed' && (isAssignee || isHandoffRecipient)) ||
      (currentStatus === 'in_progress' || currentStatus === 'wip'));

  // 6. Close ticket (Owner only, shown when not eligible for Verify & close)
  let closeEnabled = false;
  let closeDisabledReason = '';
  if (!isOwner) {
    closeDisabledReason = 'Only the organization owner can close tickets.';
  } else if (currentStatus === 'closed') {
    closeDisabledReason = 'Ticket is already closed.';
  } else if (currentStatus === 'resolved') {
    closeEnabled = true; // Legacy resolved tickets can be closed by owner
  } else if (currentStatus === 'open') {
    closeDisabledReason =
      'Cannot close an open ticket. Ticket must be in progress or passed to the owner before closing.';
  } else if (!isVerifyCloseEligible) {
    closeDisabledReason =
      'Owner can only close a ticket when it is passed to you or in progress.';
  } else {
    closeEnabled = true;
  }

  // 7. Reopen (Owner only)
  let reopenEnabled = false;
  let reopenDisabledReason = '';
  if (!isOwner) {
    reopenDisabledReason = 'Only the organization owner can reopen tickets.';
  } else if (currentStatus === 'closed' || currentStatus === 'resolved') {
    reopenEnabled = true;
  } else {
    reopenDisabledReason = 'Only closed or resolved tickets can be reopened.';
  }

  // 8. Reassign (Owner only, any ticket that is not closed)
  let reassignEnabled = false;
  let reassignDisabledReason = '';
  if (!isOwner) {
    reassignDisabledReason = 'Only organization owners can directly reassign tickets.';
  } else if (currentStatus === 'closed') {
    reassignDisabledReason = 'Cannot reassign a closed ticket. Reopen it first.';
  } else {
    reassignEnabled = true;
  }

  // Helper to render action items with accessible tooltip
  const renderActionItem = ({
    label,
    icon,
    enabled,
    disabledReason,
    highlight = false,
    onClick,
  }) => {
    return (
      <div key={label} className="relative group">
        <button
          type="button"
          role="menuitem"
          disabled={!enabled}
          aria-disabled={!enabled}
          title={!enabled ? disabledReason : label}
          onClick={enabled ? onClick : undefined}
          className={`w-full text-left px-3 py-2 rounded-lg text-body-sm font-medium flex items-center justify-between transition-all ${
            highlight && enabled
              ? 'bg-status-resolved/15 text-status-resolved hover:bg-status-resolved/25 font-semibold'
              : enabled
              ? 'hover:bg-canvas-lavender text-text-primary'
              : 'opacity-40 cursor-not-allowed text-text-muted hover:bg-transparent'
          }`}
        >
          <div className="flex items-center gap-2 min-w-0">
            <span className="material-symbols-outlined text-lg shrink-0">{icon}</span>
            <span className="truncate">{label}</span>
          </div>
          {!enabled && (
            <span
              className="material-symbols-outlined text-xs text-text-muted shrink-0"
              title={disabledReason}
            >
              lock
            </span>
          )}
        </button>

        {/* Hover / Focus Tooltip for disabled reasons */}
        {!enabled && disabledReason && (
          <div
            role="tooltip"
            className="pointer-events-none absolute right-full top-1/2 -translate-y-1/2 mr-2 w-56 p-2 rounded-lg bg-inverse-surface text-inverse-on-surface text-xs shadow-xl opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 transition-opacity z-50 leading-relaxed font-normal"
          >
            {disabledReason}
            <div className="absolute top-1/2 -right-1 -translate-y-1/2 border-4 border-transparent border-l-inverse-surface" />
          </div>
        )}
      </div>
    );
  };

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center min-h-96 text-text-secondary">
        <span className="material-symbols-outlined text-4xl animate-spin text-btn-primary mb-3">sync</span>
        <p className="font-label-md text-label-md">Loading ticket specifications...</p>
      </div>
    );
  }

  if (error || !ticket) {
    return (
      <div className="p-space-xl max-w-2xl mx-auto my-12 bg-surface-card rounded-xl shadow-sm text-center space-y-4">
        <span className="material-symbols-outlined text-5xl text-severity-blocker">error_outline</span>
        <h2 className="font-headline-md text-headline-md text-text-primary">Ticket Not Found</h2>
        <p className="font-body-md text-body-md text-text-secondary">{error || 'The requested ticket does not exist.'}</p>
        <button
          onClick={() => navigate('/workspace/tickets')}
          className="px-4 py-2 bg-btn-primary text-on-primary font-label-md text-label-md rounded-lg shadow-sm"
        >
          Return to Tickets
        </button>
      </div>
    );
  }

  const severityInfo = SEVERITY_CONFIG[ticket.severity] || SEVERITY_CONFIG.minor;
  const statusInfo = STATUS_CONFIG[ticket.status] || STATUS_CONFIG.open;

  // Status display label: "Passed to <name>" when passed, else standard label
  const displayStatusLabel = ticket.status === 'passed'
    ? `Passed to ${ticket.assignee?.full_name || ticket.assignee?.email || 'colleague'}`
    : (statusInfo.label || ticket.status);

  return (
    <div className="flex flex-col w-full min-h-screen bg-canvas-main pb-space-2xl">
      {/* SLA Breach Ambient Warning Banner */}
      {isBreached && (
        <div className="relative overflow-hidden bg-gradient-to-r from-error/15 via-error/10 to-surface-container-high px-space-lg py-space-sm flex flex-wrap items-center justify-between gap-space-md shadow-sm border-b border-severity-blocker/20">
          <div className="flex items-center gap-space-sm min-w-0">
            <div className="w-8 h-8 rounded-lg bg-severity-blocker text-on-error flex items-center justify-center shrink-0 animate-pulse">
              <span className="material-symbols-outlined text-lg">crisis_alert</span>
            </div>
            <div className="flex flex-col sm:flex-row sm:items-center gap-1 sm:gap-3">
              <div className="flex items-center gap-1.5">
                <span className="font-label-md text-label-md text-severity-blocker uppercase tracking-wider font-bold">
                  SLA Target Breached
                </span>
                <span className="font-mono font-label-md text-label-md px-2 py-0.5 rounded-full bg-severity-blocker text-on-error font-bold">
                  OVERDUE
                </span>
              </div>
              <p className="font-body-sm text-body-sm text-text-secondary truncate">
                Resolution timeline for incident exceeded target deadline.
              </p>
            </div>
          </div>
          <div className="flex items-center gap-space-xs shrink-0">
            <Link
              to="/workspace/sla"
              className="h-8 px-3 rounded-lg bg-surface-card hover:bg-canvas-lavender text-text-primary font-label-sm text-label-sm shadow-sm transition-colors flex items-center gap-1.5"
            >
              <span>SLA Overview</span>
              <span className="material-symbols-outlined text-xs">open_in_new</span>
            </Link>
            {acceptEnabled && (
              <button
                onClick={handleAcceptTicket}
                disabled={isAccepting}
                className="h-8 px-3 rounded-lg bg-surface-card hover:bg-canvas-lavender text-text-primary font-label-sm text-label-sm shadow-sm transition-colors flex items-center gap-1.5 disabled:opacity-60 disabled:cursor-not-allowed"
              >
                <span className="material-symbols-outlined text-sm text-severity-blocker">notifications_active</span>
                <span>{isAccepting ? 'Accepting...' : 'Acknowledge & Accept'}</span>
              </button>
            )}
          </div>
        </div>
      )}

      {/* Primary Workspace Content */}
      <div className="p-space-lg max-w-7xl w-full mx-auto space-y-space-lg">
        {/* Error Notification Banner (Handles 403 & 409) */}
        {actionError && (
          <div
            role="alert"
            aria-live="polite"
            className="p-4 rounded-xl bg-error-container text-on-error-container font-body-sm text-body-sm flex items-start justify-between gap-3 shadow-md border border-severity-blocker/30 animate-in fade-in"
          >
            <div className="flex items-start gap-2.5">
              <span className="material-symbols-outlined text-xl text-severity-blocker shrink-0 mt-0.5">
                error
              </span>
              <div>
                <p className="font-semibold text-text-primary">Action Failed</p>
                <p className="text-text-secondary mt-0.5">{actionError}</p>
                <p className="text-xs text-text-muted mt-1">Ticket state has been refreshed from the server.</p>
              </div>
            </div>
            <button
              onClick={() => setActionError(null)}
              className="p-1 rounded-md text-text-muted hover:text-text-primary hover:bg-canvas-lavender transition-colors"
              aria-label="Dismiss error notification"
            >
              <span className="material-symbols-outlined text-base">close</span>
            </button>
          </div>
        )}

        {/* Header & Action Row */}
        <div className="bg-surface-card rounded-xl p-space-lg shadow-sm space-y-space-md">
          {/* Breadcrumb + Ticket ID + Tags */}
          <div className="flex flex-wrap items-center justify-between gap-space-md">
            <div className="flex flex-wrap items-center gap-2 text-text-secondary">
              <div className="flex items-center gap-1.5 font-label-md text-label-md text-text-muted">
                <Link to="/workspace/tickets" className="hover:text-primary cursor-pointer transition-colors">
                  Tickets
                </Link>
                <span className="material-symbols-outlined text-xs">chevron_right</span>
                <span className="text-primary font-bold">{ticket.ticket_number}</span>
              </div>
              <span className="text-text-muted text-xs">•</span>

              {/* Severity Pill */}
              <div className={`flex items-center gap-1 px-2.5 py-0.5 rounded-full ${severityInfo.bgBadge} ${severityInfo.textBadge} font-label-sm text-label-sm font-semibold capitalize`}>
                <span className={`w-1.5 h-1.5 rounded-full ${severityInfo.dot}`}></span>
                {severityInfo.shortLabel}
              </div>

              {/* Status Pill (Displays "Passed to <name>" when passed) */}
              <div className={`flex items-center gap-1 px-2.5 py-0.5 rounded-full ${statusInfo.bg} ${statusInfo.text} font-label-sm text-label-sm font-semibold`}>
                <span className={`w-1.5 h-1.5 rounded-full ${statusInfo.dot}`}></span>
                <span>{displayStatusLabel}</span>
              </div>

              {ticket.project && (
                <span className="px-2 py-0.5 rounded-md bg-canvas-lavender text-text-secondary font-label-sm text-label-sm font-medium">
                  {ticket.project.title || ticket.project.name}
                </span>
              )}
            </div>

            {/* Quick Top Actions */}
            <div className="flex items-center gap-space-xs relative">
              {/* Lifecycle Actions Dropdown (Replaces free Change Status dropdown) */}
              <div className="relative inline-block" ref={actionMenuRef}>
                <button
                  type="button"
                  id="ticket-actions-trigger"
                  aria-haspopup="menu"
                  aria-expanded={isActionMenuOpen}
                  onClick={() => setIsActionMenuOpen((prev) => !prev)}
                  className="h-9 px-3.5 rounded-lg bg-btn-primary hover:bg-btn-primary/90 text-on-primary font-label-md text-label-md flex items-center gap-1.5 transition-colors shadow-sm font-semibold"
                >
                  <span className="material-symbols-outlined text-base">bolt</span>
                  <span>Actions</span>
                  <span className="material-symbols-outlined text-xs">
                    {isActionMenuOpen ? 'expand_less' : 'expand_more'}
                  </span>
                </button>

                {isActionMenuOpen && (
                  <div
                    role="menu"
                    aria-labelledby="ticket-actions-trigger"
                    className="absolute right-0 mt-1 w-64 bg-surface-card rounded-xl shadow-xl border border-border-subtle p-1.5 z-40 animate-in fade-in zoom-in-95 duration-150 space-y-1"
                  >
                    <div className="px-2.5 py-1 text-[11px] font-bold text-text-muted uppercase tracking-wider border-b border-border-subtle pb-1">
                      Available Actions
                    </div>

                    {/* Accept ticket */}
                    {renderActionItem({
                      label: isAccepting ? 'Accepting ticket...' : 'Accept ticket',
                      icon: 'check_circle',
                      enabled: acceptEnabled && !isAccepting,
                      disabledReason: isAccepting ? 'Accepting ticket in progress...' : acceptDisabledReason,
                      onClick: handleAcceptTicket,
                    })}

                    {/* Pass to colleague */}
                    {renderActionItem({
                      label: 'Pass to colleague',
                      icon: 'forward',
                      enabled: passEnabled,
                      disabledReason: passDisabledReason,
                      onClick: () => {
                        setIsActionMenuOpen(false);
                        setIsPassModalOpen(true);
                      },
                    })}

                    {/* Put on hold */}
                    {renderActionItem({
                      label: 'Put on hold',
                      icon: 'pause_circle',
                      enabled: holdEnabled,
                      disabledReason: holdDisabledReason,
                      onClick: () => {
                        setIsActionMenuOpen(false);
                        setIsHoldModalOpen(true);
                      },
                    })}

                    {/* Resume work */}
                    {renderActionItem({
                      label: 'Resume work',
                      icon: 'play_arrow',
                      enabled: resumeEnabled,
                      disabledReason: resumeDisabledReason,
                      onClick: handleResumeTicket,
                    })}

                    {/* Owner Governance Section */}
                    <div className="px-2.5 pt-2 py-0.5 text-[11px] font-bold text-text-muted uppercase tracking-wider border-t border-border-subtle">
                      Owner Governance
                    </div>

                    {/* Verify & close (Owner only, shown only when passed to owner or held by owner in progress) */}
                    {isVerifyCloseEligible &&
                      renderActionItem({
                        label: 'Verify & close',
                        icon: 'verified',
                        enabled: true,
                        disabledReason: '',
                        highlight: true,
                        onClick: () => {
                          setIsActionMenuOpen(false);
                          setIsVerifyCloseModalOpen(true);
                        },
                      })}

                    {/* Close ticket (Owner only, shown when not in verify & close mode) */}
                    {!isVerifyCloseEligible &&
                      renderActionItem({
                        label: 'Close ticket',
                        icon: 'close_fullscreen',
                        enabled: closeEnabled,
                        disabledReason: closeDisabledReason,
                        onClick: () => {
                          setIsActionMenuOpen(false);
                          setIsCloseModalOpen(true);
                        },
                      })}

                    {/* Reopen ticket (Owner only) */}
                    {renderActionItem({
                      label: 'Reopen ticket',
                      icon: 'restart_alt',
                      enabled: reopenEnabled,
                      disabledReason: reopenDisabledReason,
                      onClick: () => {
                        setIsActionMenuOpen(false);
                        setIsReopenModalOpen(true);
                      },
                    })}

                    {/* Reassign (Owner only, any ticket that is not closed) */}
                    {renderActionItem({
                      label: 'Reassign (Owner)',
                      icon: 'assignment_ind',
                      enabled: reassignEnabled,
                      disabledReason: reassignDisabledReason,
                      onClick: () => {
                        setIsActionMenuOpen(false);
                        setIsReassignModalOpen(true);
                      },
                    })}
                  </div>
                )}
              </div>

              {/* Change Severity Dropdown */}
              <div className="relative inline-block" ref={severityMenuRef}>
                <button
                  type="button"
                  onClick={() => setIsSeverityMenuOpen(prev => !prev)}
                  className="h-9 px-3.5 rounded-lg bg-canvas-lavender hover:bg-surface-container text-text-primary font-label-md text-label-md flex items-center gap-1.5 transition-colors shadow-sm"
                >
                  <span className="material-symbols-outlined text-base">priority_high</span>
                  <span>Severity</span>
                  <span className="material-symbols-outlined text-xs">expand_more</span>
                </button>
                {isSeverityMenuOpen && (
                  <div className="absolute right-0 mt-1 w-40 bg-surface-card rounded-xl shadow-lg border border-border-subtle py-1 z-30 animate-in fade-in">
                    {TICKET_SEVERITIES.map(sev => (
                      <button
                        key={sev}
                        onClick={() => handleSeverityChange(sev)}
                        className={`w-full text-left px-3 py-2 text-label-sm font-label-sm flex items-center justify-between hover:bg-canvas-lavender ${ticket.severity === sev ? 'text-primary font-bold' : 'text-text-primary'}`}
                      >
                        <span>{SEVERITY_CONFIG[sev]?.shortLabel}</span>
                        {ticket.severity === sev && <span className="material-symbols-outlined text-sm">check</span>}
                      </button>
                    ))}
                  </div>
                )}
              </div>

              {/* Back to list */}
              <button
                type="button"
                onClick={() => navigate('/workspace/tickets')}
                className="h-9 px-3 rounded-lg bg-canvas-lavender hover:bg-surface-container text-text-secondary flex items-center justify-center transition-colors shadow-sm"
                title="Return to tickets list"
              >
                <span className="material-symbols-outlined text-lg">close</span>
              </button>
            </div>
          </div>

          {/* Main Ticket Title & Editable Row */}
          <div className="flex items-start justify-between gap-space-md pt-space-xs">
            <div className="flex items-baseline gap-space-sm group flex-1">
              {isEditingTitle ? (
                <div className="flex items-center gap-2 w-full max-w-2xl">
                  <input
                    type="text"
                    value={titleInput}
                    onChange={(e) => setTitleInput(e.target.value)}
                    className="w-full text-headline-lg font-headline-lg text-text-primary px-3 py-1 bg-canvas-lavender rounded-lg border border-primary outline-none"
                    autoFocus
                  />
                  <button
                    onClick={handleSaveTitle}
                    className="px-3 py-1.5 bg-btn-primary text-on-primary rounded-lg font-label-sm text-label-sm"
                  >
                    Save
                  </button>
                  <button
                    onClick={() => {
                      setTitleInput(ticket.title);
                      setIsEditingTitle(false);
                    }}
                    className="px-3 py-1.5 bg-canvas-lavender text-text-secondary rounded-lg font-label-sm text-label-sm"
                  >
                    Cancel
                  </button>
                </div>
              ) : (
                <>
                  <h1 className="font-headline-lg text-headline-lg text-text-primary tracking-tight">
                    {ticket.title}
                  </h1>
                  <button
                    onClick={() => setIsEditingTitle(true)}
                    className="p-1 text-text-muted hover:text-primary transition-colors opacity-80 group-hover:opacity-100"
                    title="Edit title"
                  >
                    <span className="material-symbols-outlined text-lg">edit</span>
                  </button>
                </>
              )}
            </div>

            <div className="hidden xl:flex items-center gap-3 shrink-0 text-text-secondary font-body-sm text-body-sm bg-canvas-lavender/60 px-3 py-1.5 rounded-lg">
              <span className="flex items-center gap-1">
                <span className="material-symbols-outlined text-base text-text-muted">schedule</span>
                Created: {new Date(ticket.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
              </span>
              {ticket.deadline && (
                <>
                  <span className="text-text-muted">•</span>
                  <span className={`flex items-center gap-1 ${isBreached ? 'text-severity-blocker font-bold' : 'text-text-secondary'}`}>
                    <span className="material-symbols-outlined text-base">warning</span>
                    Target: {new Date(ticket.deadline).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                  </span>
                </>
              )}
            </div>
          </div>
        </div>

        {/* Asymmetric Bento Grid: Left (Technical Context & Feed) | Right (Metadata, Attachments, Audit Log) */}
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-space-lg items-start">
          {/* LEFT COLUMN: 8 Columns */}
          <div className="lg:col-span-8 space-y-space-lg">
            {/* SLA Milestone Flow & KPI Section */}
            <div className="bg-surface-card rounded-xl p-space-lg shadow-sm space-y-space-lg">
              <div className="flex items-center justify-between border-b border-border-subtle pb-space-sm">
                <div className="flex items-center gap-2">
                  <span className="material-symbols-outlined text-primary text-xl">acute</span>
                  <h2 className="font-headline-sm text-headline-sm text-text-primary">Incident SLA Tracking &amp; Milestones</h2>
                </div>
                <div className="flex items-center gap-3">
                  <span className="font-label-sm text-label-sm text-text-muted uppercase tracking-wider hidden sm:inline">
                    {ticket.severity === 'blocker' ? 'Tier-1 P0 Agreement' : 'Standard Service Level'}
                  </span>
                  <Link
                    to="/workspace/sla"
                    className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md bg-canvas-lavender hover:bg-primary-fixed text-primary font-label-sm text-xs font-semibold transition-colors"
                  >
                    <span>SLA Overview</span>
                    <span className="material-symbols-outlined text-xs">arrow_forward</span>
                  </Link>
                </div>
              </div>

              {/* Horizontal Stepper Visualization */}
              <div className="py-2">
                <div className="grid grid-cols-1 sm:grid-cols-4 gap-3 relative">
                  {/* Step 1: Created */}
                  <div className="flex flex-col space-y-1.5 p-3 rounded-lg bg-canvas-lavender/80">
                    <div className="flex items-center justify-between">
                      <span className="w-6 h-6 rounded-full bg-status-resolved text-white flex items-center justify-center font-label-sm text-label-sm">
                        <span className="material-symbols-outlined text-sm">check</span>
                      </span>
                      <span className="font-mono text-body-sm font-semibold text-text-primary">
                        {new Date(ticket.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                      </span>
                    </div>
                    <p className="font-label-md text-label-md text-text-primary pt-1">Created</p>
                    <p className="font-body-sm text-body-sm text-text-secondary">Incident ingested</p>
                  </div>

                  {/* Step 2: First Response Met */}
                  <div className="flex flex-col space-y-1.5 p-3 rounded-lg bg-status-resolved/10">
                    <div className="flex items-center justify-between">
                      <span className="w-6 h-6 rounded-full bg-status-resolved text-white flex items-center justify-center font-label-sm text-label-sm">
                        <span className="material-symbols-outlined text-sm">task_alt</span>
                      </span>
                      <span className="font-mono text-body-sm font-semibold text-status-resolved">
                        {ticket.first_response_at ? new Date(ticket.first_response_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : 'Pending'}
                      </span>
                    </div>
                    <p className="font-label-md text-label-md text-text-primary pt-1">First Response</p>
                    <p className="font-body-sm text-body-sm text-status-resolved font-medium">
                      {ticket.first_response_at ? 'Target Met' : 'Awaiting Triage'}
                    </p>
                  </div>

                  {/* Step 3: Target Limit */}
                  <div className="flex flex-col space-y-1.5 p-3 rounded-lg bg-canvas-lavender/80">
                    <div className="flex items-center justify-between">
                      <span className={`w-6 h-6 rounded-full ${isBreached ? 'bg-severity-blocker text-white' : 'bg-outline text-white'} flex items-center justify-center font-label-sm text-label-sm`}>
                        <span className="material-symbols-outlined text-sm">{isBreached ? 'alarm_off' : 'schedule'}</span>
                      </span>
                      <span className="font-mono text-body-sm font-semibold text-text-primary">
                        {ticket.deadline ? new Date(ticket.deadline).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : 'Flexible'}
                      </span>
                    </div>
                    <p className="font-label-md text-label-md text-text-primary pt-1">Resolution Target</p>
                    <p className="font-body-sm text-body-sm text-text-secondary">
                      {ticket.deadline ? new Date(ticket.deadline).toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' }) : 'Expected cutoff'}
                    </p>
                  </div>

                  {/* Step 4: Resolution Status */}
                  <div className={`flex flex-col space-y-1.5 p-3 rounded-lg ${ticket.status === 'resolved' || ticket.status === 'closed' ? 'bg-status-resolved/15' : isBreached ? 'bg-severity-blocker/15' : 'bg-canvas-lavender'}`}>
                    <div className="flex items-center justify-between">
                      <span className={`w-6 h-6 rounded-full ${ticket.status === 'resolved' ? 'bg-status-resolved text-white' : isBreached ? 'bg-severity-blocker text-white animate-pulse' : 'bg-outline text-white'} flex items-center justify-center font-label-sm text-label-sm`}>
                        <span className="material-symbols-outlined text-sm">
                          {ticket.status === 'resolved' ? 'done_all' : isBreached ? 'priority_high' : 'sync'}
                        </span>
                      </span>
                      <span className={`font-mono text-body-sm font-bold ${isBreached ? 'text-severity-blocker' : 'text-text-primary'}`}>
                        {statusInfo.label}
                      </span>
                    </div>
                    <p className={`font-label-md text-label-md ${isBreached ? 'text-severity-blocker font-bold' : 'text-text-primary'} pt-1`}>
                      {isBreached ? `Breached (+${ticket.overrun_duration || 'Overdue'})` : statusInfo.label}
                    </p>
                    <p className="font-body-sm text-body-sm text-text-secondary">Current lifecycle</p>
                  </div>
                </div>
              </div>
            </div>

            {/* Root Cause Analysis (RCA) Container */}
            <div className="bg-surface-card rounded-xl p-space-lg shadow-sm space-y-space-md">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="w-7 h-7 rounded-lg bg-primary-container text-on-primary-container flex items-center justify-center">
                    <span className="material-symbols-outlined text-base">clinical_notes</span>
                  </span>
                  <div>
                    <h3 className="font-headline-sm text-headline-sm text-text-primary">Formal Root Cause Analysis (RCA)</h3>
                    <span className="font-body-sm text-body-sm text-text-muted">Distinct from informal triage chatter</span>
                  </div>
                </div>
                <span className="px-2 py-0.5 rounded-full bg-status-on-hold/15 text-status-on-hold font-label-sm text-label-sm font-semibold">
                  Triage Active
                </span>
              </div>
              <div className="p-space-md rounded-xl bg-canvas-lavender/60 space-y-3 font-body-sm text-body-sm text-text-primary">
                <div>
                  <span className="font-label-md text-label-md text-text-primary font-bold">Trigger Event:</span>
                  <p className="text-text-secondary mt-0.5">
                    {ticket.rca_trigger || 'Awaiting initial technical investigation and root-cause verification.'}
                  </p>
                </div>
                <div>
                  <span className="font-label-md text-label-md text-text-primary font-bold">Mitigation in Flight:</span>
                  <p className="text-text-secondary mt-0.5">
                    {ticket.rca_mitigation || 'Standard remediation protocol in progress under current sprint.'}
                  </p>
                </div>
                <div>
                  <span className="font-label-md text-label-md text-text-primary font-bold">Action Item Tracking:</span>
                  <p className="text-text-secondary mt-0.5">
                    {ticket.rca_action_items || 'No linked corrective tasks registered yet.'}
                  </p>
                </div>
              </div>
            </div>

            {/* Handoff History Timeline */}
            <TicketHandoffTimeline
              ticketId={id}
              initialHandoffs={ticket.handoffs || []}
            />

            {/* Discussion & Triage Feed */}
            <div className="bg-surface-card rounded-xl p-space-lg shadow-sm space-y-space-lg">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="material-symbols-outlined text-primary text-xl">forum</span>
                  <h3 className="font-headline-sm text-headline-sm text-text-primary">Discussion &amp; Triage Feed</h3>
                  <span className="px-2 py-0.5 rounded-full bg-canvas-lavender text-text-secondary font-label-sm text-label-sm font-semibold">
                    {ticket.comments?.length || 0} items
                  </span>
                </div>
              </div>

              {/* Feed items */}
              <TicketCommentFeed
                comments={ticket.comments}
                ticketId={id}
                onAttachmentDeleted={fetchTicket}
              />

              {/* Comment Composer */}
              <TicketCommentBox
                ticketId={id}
                onCommentAdded={fetchTicket}
              />
            </div>
          </div>

          {/* RIGHT COLUMN: 4 Columns (Metadata, Artifacts, Audit Trail) */}
          <div className="lg:col-span-4 space-y-space-lg">
            {/* Related Task Card */}
            <RelatedTaskCard
              ticket={ticket}
              canEdit={canEditTicket}
              onTicketUpdated={(updated) => {
                setTicket(updated);
                fetchTicket();
              }}
            />

            {/* Structured Metadata Card */}
            <div className="bg-surface-card rounded-xl p-space-lg shadow-sm space-y-space-md">
              <div className="flex items-center justify-between border-b border-border-subtle pb-space-sm">
                <h2 className="font-headline-sm text-headline-sm text-text-primary flex items-center gap-2">
                  <span className="material-symbols-outlined text-primary text-xl">info</span>
                  Ticket Metadata
                </h2>
              </div>
              <div className="space-y-space-md font-body-sm text-body-sm">
                {/* Current Status */}
                <div className="flex items-center justify-between">
                  <span className="text-text-secondary">Current Status</span>
                  <span className={`px-2.5 py-0.5 rounded-full ${statusInfo.bg} ${statusInfo.text} font-label-sm text-label-sm font-semibold flex items-center gap-1`}>
                    <span className={`w-1.5 h-1.5 rounded-full ${statusInfo.dot}`}></span>
                    <span>{displayStatusLabel}</span>
                  </span>
                </div>

                {/* Reported By */}
                <div className="flex items-center justify-between">
                  <span className="text-text-secondary">Reported By</span>
                  <div className="flex items-center gap-2">
                    <div className="w-6 h-6 rounded-full bg-secondary-container text-on-secondary-container flex items-center justify-center font-label-sm text-label-sm font-semibold">
                      {ticket.reporter?.full_name ? ticket.reporter.full_name.slice(0, 2).toUpperCase() : 'RP'}
                    </div>
                    <span className="font-label-md text-label-md text-text-primary">
                      {ticket.reporter?.full_name || 'System Reporter'}
                    </span>
                  </div>
                </div>

                {/* Assigned To (Kept in sync) */}
                <div className="flex items-center justify-between">
                  <span className="text-text-secondary">Assigned To</span>
                  <div className="flex items-center gap-2">
                    <div className="w-6 h-6 rounded-full bg-primary-container text-on-primary-container flex items-center justify-center font-label-sm text-label-sm font-semibold">
                      {ticket.assignee?.full_name ? ticket.assignee.full_name.slice(0, 2).toUpperCase() : 'UN'}
                    </div>
                    <span className="font-label-md text-label-md text-text-primary font-medium">
                      {ticket.assignee?.full_name || 'Unassigned'}
                    </span>
                  </div>
                </div>

                {/* Project */}
                <div className="flex items-center justify-between">
                  <span className="text-text-secondary">Project</span>
                  <span className="font-label-md text-label-md text-primary font-semibold">
                    {ticket.project?.title || ticket.project?.name || 'General Workspace'}
                  </span>
                </div>

                {/* Created Time */}
                <div className="flex items-center justify-between">
                  <span className="text-text-secondary">Created Date</span>
                  <span className="font-mono text-text-primary">
                    {new Date(ticket.created_at).toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' })}
                  </span>
                </div>

                {/* Last Updated */}
                <div className="flex items-center justify-between">
                  <span className="text-text-secondary">Last Updated</span>
                  <span className="font-mono text-status-wip font-semibold">
                    {ticket.updated_at ? new Date(ticket.updated_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : 'Just now'}
                  </span>
                </div>

                {/* Expected Deadline */}
                <div className="flex items-center justify-between">
                  <span className="text-text-secondary">Target Deadline</span>
                  <span className={`font-mono font-bold ${isBreached ? 'text-severity-blocker' : 'text-text-primary'}`}>
                    {ticket.deadline ? new Date(ticket.deadline).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' }) : 'None'}
                  </span>
                </div>

                {/* Severity */}
                <div className="flex items-center justify-between">
                  <span className="text-text-secondary">Severity Tier</span>
                  <span className={`px-2 py-0.5 rounded ${severityInfo.bgBadge} ${severityInfo.textBadge} font-label-sm text-label-sm font-bold uppercase`}>
                    {severityInfo.shortLabel}
                  </span>
                </div>
              </div>
            </div>

            {/* Attachments & Artifacts */}
            <TicketAttachmentsCard
              ticketId={id}
              initialAttachments={ticket.attachments || []}
              onAttachmentChange={fetchTicket}
            />

            {/* Audit Log & Event Trail */}
            <div className="bg-surface-card rounded-xl p-space-lg shadow-sm space-y-space-md">
              <div className="flex items-center justify-between border-b border-border-subtle pb-space-sm">
                <div className="flex items-center gap-2">
                  <span className="material-symbols-outlined text-primary text-xl">history</span>
                  <h2 className="font-headline-sm text-headline-sm text-text-primary">Audit Log &amp; Event Trail</h2>
                </div>
                <span className="font-label-sm text-label-sm text-text-muted">Live Stream</span>
              </div>

              <div className="space-y-space-md relative before:absolute before:left-3 before:top-2 before:bottom-2 before:w-0.5 before:bg-canvas-lavender">
                {(!ticket.activities || ticket.activities.length === 0) ? (
                  <p className="text-xs text-text-muted py-2 pl-6">No historical audit logs recorded yet.</p>
                ) : (
                  ticket.activities.map((act) => (
                    <div key={act.id} className="flex items-start gap-3 relative">
                      <div className="w-6 h-6 rounded-full bg-btn-primary text-on-primary flex items-center justify-center shrink-0 z-10 shadow-sm">
                        <span className="material-symbols-outlined text-xs">sync_alt</span>
                      </div>
                      <div className="space-y-1 min-w-0">
                        <p className="font-body-sm text-body-sm text-text-primary leading-tight">
                          <strong className="font-semibold">{act.action}</strong>:{' '}
                          {act.action === 'reassigned' && (act.new_value?.startsWith('usr_') || act.old_value?.startsWith('usr_'))
                            ? `Reassigned to ${ticket.assignee?.full_name || ticket.assignee?.display_name || 'team member'}`
                            : (act.new_value?.includes('usr_') || act.old_value?.includes('usr_'))
                              ? (act.new_value || act.old_value || 'Lifecycle update').replace(/usr_[a-zA-Z0-9_-]+/g, ticket.assignee?.full_name || 'team member')
                              : (act.new_value || act.old_value || 'Lifecycle update')}
                        </p>
                        <p className="font-mono text-xs text-text-muted">
                          {new Date(act.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })} • {act.actor?.full_name || act.actor?.display_name || 'User Action'}
                        </p>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* ===================================================================== */}
      {/* Action Modals */}
      {/* ===================================================================== */}

      {/* Pass to Colleague Modal */}
      <PassOrReassignModal
        isOpen={isPassModalOpen}
        onClose={() => setIsPassModalOpen(false)}
        ticket={ticket}
        mode="pass"
        onSuccess={(updated) => {
          setTicket(updated);
          fetchTicket();
        }}
        onError={(msg) => {
          setActionError(msg);
        }}
      />

      {/* Owner Reassign Modal */}
      <PassOrReassignModal
        isOpen={isReassignModalOpen}
        onClose={() => setIsReassignModalOpen(false)}
        ticket={ticket}
        mode="reassign"
        onSuccess={(updated) => {
          setTicket(updated);
          fetchTicket();
        }}
        onError={(msg) => {
          setActionError(msg);
        }}
      />

      {/* Put on Hold Modal */}
      <HoldTicketModal
        isOpen={isHoldModalOpen}
        onClose={() => setIsHoldModalOpen(false)}
        ticket={ticket}
        onSuccess={(updated) => {
          setTicket(updated);
          fetchTicket();
        }}
        onError={(msg) => {
          setActionError(msg);
          fetchTicket();
        }}
      />

      {/* Verify & Close Modal (Owner Only) */}
      <CloseTicketModal
        isOpen={isVerifyCloseModalOpen}
        onClose={() => setIsVerifyCloseModalOpen(false)}
        ticket={ticket}
        isVerifyClose={true}
        onUpdated={(updated) => {
          setTicket(updated);
          fetchTicket();
        }}
        onError={(msg) => {
          setActionError(msg);
          fetchTicket();
        }}
      />

      {/* Close Ticket Modal (Owner Only) */}
      <CloseTicketModal
        isOpen={isCloseModalOpen}
        onClose={() => setIsCloseModalOpen(false)}
        ticket={ticket}
        isVerifyClose={false}
        onUpdated={(updated) => {
          setTicket(updated);
          fetchTicket();
        }}
        onError={(msg) => {
          setActionError(msg);
          fetchTicket();
        }}
      />

      {/* Reopen Ticket Modal (Owner Only) */}
      <ReopenTicketModal
        isOpen={isReopenModalOpen}
        onClose={() => setIsReopenModalOpen(false)}
        ticket={ticket}
        onUpdated={(updated) => {
          setTicket(updated);
          fetchTicket();
        }}
        onError={(msg) => {
          setActionError(msg);
          fetchTicket();
        }}
      />
    </div>
  );
};

export default TicketDetailsPage;
