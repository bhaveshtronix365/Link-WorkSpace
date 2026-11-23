import React, { useState, useEffect, useRef } from 'react';
import { ticketsService } from '../../../services/ticketsService';

export const CloseTicketModal = ({
  isOpen,
  onClose,
  ticket,
  isVerifyClose = false,
  onUpdated,
  onError,
}) => {
  const [resolutionSummary, setResolutionSummary] = useState('');
  const [commitSha, setCommitSha] = useState('');
  const [prReference, setPrReference] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const submittingRef = useRef(false);

  useEffect(() => {
    if (isOpen && ticket) {
      setResolutionSummary(isVerifyClose ? 'Verified and closed by organization owner.' : '');
      setCommitSha('');
      setPrReference('');
      setError(null);
      setSubmitting(false);
      submittingRef.current = false;
    }
  }, [isOpen, ticket, isVerifyClose]);

  if (!isOpen || !ticket) return null;

  const handleSubmit = async (e) => {
    e?.preventDefault?.();
    if (submittingRef.current) return;
    submittingRef.current = true;

    const summary = resolutionSummary.trim() || (isVerifyClose ? 'Verified and closed by organization owner.' : '');

    setSubmitting(true);
    setError(null);

    try {
      const prOrCommit = [commitSha ? `Commit: ${commitSha}` : null, prReference ? `PR: ${prReference}` : null]
        .filter(Boolean)
        .join(' • ');

      const payload = {
        message: summary,
        resolution_summary: summary,
        verified_by: isVerifyClose ? 'Owner Verified' : 'Reporter',
        pr_or_commit: prOrCommit || undefined,
        operational_signoff: true,
        version: ticket.version ?? 0,
      };

      const updated = await ticketsService.closeTicket(ticket.id, payload);
      onUpdated?.(updated);
      onClose();
    } catch (err) {
      console.error('Failed to close ticket:', err);
      const errMsg =
        err.response?.data?.error?.message ||
        err.response?.data?.detail?.message ||
        err.response?.data?.detail ||
        err.message ||
        'Failed to close ticket. Please refresh and try again.';
      setError(errMsg);
      onError?.(errMsg);
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40 backdrop-blur-sm">
      <div
        className="w-full max-w-lg bg-surface-card rounded-xl shadow-xl overflow-hidden flex flex-col animate-in fade-in zoom-in-95 duration-200"
        role="dialog"
        aria-modal="true"
      >
        {/* Header */}
        <div className="p-space-lg pb-space-sm bg-surface-card flex items-start justify-between border-b border-border-subtle">
          <div className="space-y-1">
            <div className="flex items-center gap-space-xs">
              <span className="w-2 h-2 rounded-full bg-status-closed" />
              <span className="font-label-sm text-label-sm text-text-muted uppercase tracking-wider">
                Owner Action
              </span>
            </div>
            <h2 className="font-headline-md text-headline-md text-text-primary tracking-tight">
              {isVerifyClose ? 'Verify & Close Ticket' : 'Close Ticket'}
            </h2>
            <p className="font-body-sm text-body-sm text-text-secondary">
              {isVerifyClose
                ? 'Confirm resolution verification and finalize ticket closure.'
                : 'Permanently finalize ticket lifecycle and archive records.'}
            </p>
          </div>
          <button
            onClick={onClose}
            aria-label="Close dialog"
            className="w-8 h-8 rounded-lg flex items-center justify-center text-text-muted hover:text-text-primary hover:bg-canvas-lavender transition-colors"
          >
            <span className="material-symbols-outlined text-lg">close</span>
          </button>
        </div>

        {/* Form Body */}
        <form onSubmit={handleSubmit} className="p-space-lg space-y-space-md overflow-y-auto max-h-[75vh]">
          {error && (
            <div
              role="alert"
              aria-live="polite"
              className="p-3 rounded-lg bg-error-container text-on-error-container font-body-sm text-body-sm flex items-start gap-2"
            >
              <span className="material-symbols-outlined text-base mt-0.5 shrink-0">error</span>
              <span className="flex-1 font-semibold">{error}</span>
            </div>
          )}

          {/* Ticket Reference */}
          <div className="p-space-sm bg-canvas-lavender rounded-lg flex flex-col gap-1.5 shadow-sm">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-space-xs min-w-0">
                <span className="font-label-md text-label-md font-mono text-status-wip">
                  {ticket.ticket_number}
                </span>
                <span className="text-outline-variant">•</span>
                <span className="font-label-sm text-label-sm px-1.5 py-0.5 rounded-full bg-severity-major/20 text-text-primary font-semibold capitalize">
                  {ticket.severity}
                </span>
              </div>
              <span className="font-label-sm text-label-sm px-2.5 py-0.5 rounded-full bg-status-closed/15 text-status-closed font-semibold capitalize">
                {ticket.status}
              </span>
            </div>
            <p className="font-label-md text-label-md text-text-primary truncate">{ticket.title}</p>
          </div>

          {/* Short confirmation prompt */}
          {isVerifyClose ? (
            <div className="p-3 bg-status-resolved/10 border border-status-resolved/30 rounded-lg flex items-start gap-2 text-body-sm text-text-secondary">
              <span className="material-symbols-outlined text-status-resolved text-base mt-0.5 shrink-0">
                verified
              </span>
              <div>
                <p className="font-semibold text-text-primary">Confirmation</p>
                <p>
                  You are verifying and closing this ticket as the organization owner. This action finalizes the incident SLA and moves the ticket to closed state.
                </p>
              </div>
            </div>
          ) : (
            <div className="p-3 bg-surface-container-low rounded-lg text-body-sm text-text-secondary">
              As the organization owner, confirm that work is complete and this ticket can be closed.
            </div>
          )}

          {/* Resolution Summary / Closure Notes */}
          <div className="space-y-1.5">
            <label className="font-label-md text-label-md text-text-primary flex items-center justify-between">
              <span>Closure &amp; Verification Notes</span>
              <span className="font-label-sm text-label-sm text-text-muted">Optional</span>
            </label>
            <textarea
              rows="3"
              value={resolutionSummary}
              onChange={(e) => setResolutionSummary(e.target.value)}
              placeholder="e.g. Root cause isolated to outdated cache config. Verification confirmed in production."
              className="w-full p-3 bg-canvas-main rounded-lg border border-border-subtle font-body-sm text-body-sm text-text-primary placeholder:text-text-muted outline-none focus:border-primary transition-colors resize-y"
            />
          </div>
        </form>

        {/* Footer */}
        <div className="px-space-lg py-space-md bg-canvas-lavender flex items-center justify-end gap-space-sm border-t border-border-subtle">
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            className="h-10 px-space-md rounded-lg bg-surface-container hover:bg-surface-container-high text-text-primary font-label-md text-label-md transition-colors"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleSubmit}
            disabled={submitting}
            className={`h-10 px-space-lg font-label-md text-label-md rounded-lg flex items-center gap-1.5 shadow-sm transition-all font-semibold cursor-pointer ${
              isVerifyClose
                ? 'bg-emerald-600 hover:bg-emerald-700 active:bg-emerald-800 text-white'
                : 'bg-btn-primary hover:bg-btn-primary/90 text-on-primary'
            } disabled:opacity-50 disabled:cursor-not-allowed`}
          >
            <span className="material-symbols-outlined text-base">check_circle</span>
            <span>{submitting ? 'Closing...' : isVerifyClose ? 'Verify & Close' : 'Close Ticket'}</span>
          </button>
        </div>
      </div>
    </div>
  );
};

export default CloseTicketModal;
