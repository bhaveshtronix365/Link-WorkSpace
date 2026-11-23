import React, { useState, useEffect } from 'react';
import { Modal } from '../../../components/common/Modal';
import { ticketsService } from '../../../services/ticketsService';

export const HoldTicketModal = ({
  isOpen,
  onClose,
  ticket,
  onSuccess,
  onError,
}) => {
  const [reason, setReason] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [localError, setLocalError] = useState(null);

  useEffect(() => {
    if (isOpen && ticket) {
      setReason('');
      setLocalError(null);
      setSubmitting(false);
    }
  }, [isOpen, ticket]);

  const trimmedReason = reason.trim();
  const isValid = trimmedReason.length >= 3;

  const handleSubmit = async (e) => {
    if (e) e.preventDefault();
    if (!isValid || submitting) return;

    setSubmitting(true);
    setLocalError(null);

    try {
      const updated = await ticketsService.holdTicket(ticket.id, {
        reason: trimmedReason,
        version: ticket.version,
      });
      onSuccess?.(updated);
      onClose();
    } catch (err) {
      console.error('Failed to hold ticket:', err);
      const errMsg =
        err.response?.data?.error?.message ||
        err.response?.data?.detail?.message ||
        err.response?.data?.detail ||
        err.message ||
        'Failed to put ticket on hold.';
      setLocalError(errMsg);
      onError?.(errMsg);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Put Ticket on Hold"
      subtitle="Temporarily pause active work on this incident. A reason is required for audit tracking."
      size="md"
      hasUnsavedChanges={Boolean(trimmedReason)}
      footer={
        <div className="flex items-center justify-end gap-space-sm w-full">
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            className="px-4 py-2 rounded-lg bg-surface-container hover:bg-surface-container-high text-text-primary font-label-md text-label-md transition-colors"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleSubmit}
            disabled={!isValid || submitting}
            className="px-5 py-2 rounded-lg bg-status-on-hold text-white font-label-md text-label-md font-semibold flex items-center gap-1.5 shadow-sm transition-all disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {submitting ? (
              <>
                <span className="material-symbols-outlined text-sm animate-spin">sync</span>
                <span>Placing on Hold...</span>
              </>
            ) : (
              <>
                <span className="material-symbols-outlined text-sm">pause_circle</span>
                <span>Put on Hold</span>
              </>
            )}
          </button>
        </div>
      }
    >
      <form onSubmit={handleSubmit} className="space-y-space-md text-text-primary">
        {localError && (
          <div
            role="alert"
            aria-live="polite"
            className="p-3 rounded-lg bg-error-container text-on-error-container font-body-sm text-body-sm flex items-start gap-2"
          >
            <span className="material-symbols-outlined text-base shrink-0 mt-0.5">error</span>
            <div className="flex-1 font-semibold">{localError}</div>
          </div>
        )}

        <div className="space-y-1.5">
          <label htmlFor="hold-reason" className="font-label-md text-label-md text-text-primary flex items-center justify-between">
            <span>
              Hold Reason <span className="text-severity-blocker">*</span>
            </span>
            <span className="font-label-sm text-label-sm text-text-muted">
              Mandatory Audit Reason
            </span>
          </label>
          <textarea
            id="hold-reason"
            rows="3"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="e.g. Awaiting external vendor escalation response, customer reproduction logs, or deployment window..."
            className="w-full p-3 bg-canvas-main rounded-lg border border-border-subtle font-body-sm text-body-sm text-text-primary placeholder:text-text-muted outline-none focus:border-primary transition-colors resize-y"
            autoFocus
            required
          />
        </div>
      </form>
    </Modal>
  );
};

export default HoldTicketModal;
