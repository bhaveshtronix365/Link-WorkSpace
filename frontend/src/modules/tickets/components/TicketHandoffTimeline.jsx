import React, { useState, useEffect, useCallback } from 'react';
import { ticketsService } from '../../../services/ticketsService';
import Avatar from '../../../components/common/Avatar';

const formatBytes = (bytes, decimals = 1) => {
  if (!bytes || bytes === 0) return '0 B';
  const k = 1024;
  const dm = decimals < 0 ? 0 : decimals;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(dm))} ${sizes[i]}`;
};

const getFileIcon = (file) => {
  const name = file.original_name || file.name || file.filename || '';
  const ext = (name.split('.').pop() || '').toLowerCase();
  const mime = (file.content_type || '').toLowerCase();

  if (mime.startsWith('image/') || ['png', 'jpg', 'jpeg', 'webp', 'gif'].includes(ext)) {
    return 'image';
  }
  if (mime === 'application/pdf' || ext === 'pdf') {
    return 'picture_as_pdf';
  }
  if (['csv', 'xlsx', 'xls'].includes(ext) || mime.includes('csv') || mime.includes('spreadsheet')) {
    return 'table_chart';
  }
  if (['json', 'xml', 'yaml', 'yml'].includes(ext) || mime.includes('json')) {
    return 'data_object';
  }
  if (['txt', 'log'].includes(ext) || mime.startsWith('text/')) {
    return 'article';
  }
  return 'description';
};

/**
 * TicketHandoffTimeline
 * Displays the authoritative Handoff history timeline on the ticket detail page.
 * Shows: who passed it, to whom, when, message with attachments,
 * with distinct labels for 'reassigned by owner' vs 'passed to colleague'.
 */
export const TicketHandoffTimeline = ({ ticketId, initialHandoffs = [], refreshTrigger = 0 }) => {
  const [handoffs, setHandoffs] = useState(initialHandoffs);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [downloadingId, setDownloadingId] = useState(null);

  const fetchHandoffs = useCallback(async () => {
    if (!ticketId) return;
    try {
      setLoading(true);
      setError(null);
      const data = await ticketsService.getHandoffs(ticketId);
      setHandoffs(Array.isArray(data) ? data : []);
    } catch (err) {
      console.warn('Failed to load ticket handoffs:', err);
      // If error, fall back to initialHandoffs if available
      if (initialHandoffs && initialHandoffs.length > 0) {
        setHandoffs(initialHandoffs);
      } else {
        setError(err.response?.data?.detail || err.message || 'Unable to load handoff history.');
      }
    } finally {
      setLoading(false);
    }
  }, [ticketId, initialHandoffs]);

  useEffect(() => {
    fetchHandoffs();
  }, [fetchHandoffs, refreshTrigger]);

  const handleDownloadAttachment = async (att) => {
    try {
      setDownloadingId(att.id);
      const fileName = att.original_name || att.name || att.filename || 'attachment';
      await ticketsService.downloadAttachment(ticketId, att.id, fileName);
    } catch (err) {
      console.error('Failed to download attachment:', err);
    } finally {
      setDownloadingId(null);
    }
  };

  return (
    <section
      className="bg-surface-card rounded-xl p-space-lg shadow-sm space-y-space-md border border-border-subtle/50"
      aria-labelledby="handoff-timeline-heading"
    >
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border-subtle pb-space-sm">
        <div className="flex items-center gap-2">
          <span className="material-symbols-outlined text-primary text-xl">swap_horiz</span>
          <h2 id="handoff-timeline-heading" className="font-headline-sm text-headline-sm text-text-primary">
            Handoff History
          </h2>
          <span className="px-2 py-0.5 rounded-full bg-canvas-lavender text-text-secondary font-label-sm text-label-sm font-semibold">
            {handoffs.length} {handoffs.length === 1 ? 'event' : 'events'}
          </span>
        </div>
        <button
          type="button"
          onClick={fetchHandoffs}
          disabled={loading}
          className="text-text-muted hover:text-text-primary p-1 rounded-lg hover:bg-canvas-lavender transition-colors disabled:opacity-50"
          title="Refresh handoff history"
          aria-label="Refresh handoff history"
        >
          <span className={`material-symbols-outlined text-base ${loading ? 'animate-spin' : ''}`}>
            refresh
          </span>
        </button>
      </div>

      {/* Content */}
      {loading && handoffs.length === 0 ? (
        <div className="flex items-center justify-center py-8">
          <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-primary" />
          <span className="ml-3 font-body-sm text-text-secondary">Loading handoff history...</span>
        </div>
      ) : error && handoffs.length === 0 ? (
        <div className="p-4 bg-error-container/30 border border-error/30 rounded-xl flex items-center justify-between gap-3">
          <div className="flex items-center gap-2 text-severity-blocker">
            <span className="material-symbols-outlined text-lg">error</span>
            <span className="text-xs font-medium">{error}</span>
          </div>
          <button
            type="button"
            onClick={fetchHandoffs}
            className="text-xs font-semibold px-2 py-1 rounded bg-surface-card hover:bg-black/5 transition-colors"
          >
            Retry
          </button>
        </div>
      ) : handoffs.length === 0 ? (
        <div className="py-8 text-center border-2 border-dashed border-border-subtle/80 rounded-xl">
          <span className="material-symbols-outlined text-3xl text-text-muted mb-1">history_toggle_off</span>
          <p className="font-body-sm text-xs sm:text-body-sm text-text-muted">
            No handoff events recorded for this ticket yet.
          </p>
        </div>
      ) : (
        <div className="space-y-space-md relative before:absolute before:left-3 before:top-2 before:bottom-2 before:w-0.5 before:bg-canvas-lavender">
          {handoffs.map((h) => {
            const isReassign = h.kind === 'reassign';
            const fromName = h.from_user?.full_name || h.from_user?.email || 'Unknown User';
            const toName = h.to_user?.full_name || h.to_user?.email || 'Unknown User';
            const hasAttachments = h.attachments && h.attachments.length > 0;

            return (
              <article
                key={h.id}
                className="flex items-start gap-3 relative group"
                aria-label={`Handoff from ${fromName} to ${toName}`}
              >
                {/* Timeline node icon */}
                <div
                  className={`w-6 h-6 rounded-full flex items-center justify-center shrink-0 z-10 shadow-sm ${
                    isReassign
                      ? 'bg-purple-600 text-white ring-2 ring-purple-200'
                      : 'bg-primary text-on-primary ring-2 ring-primary/20'
                  }`}
                >
                  <span className="material-symbols-outlined text-xs">
                    {isReassign ? 'admin_panel_settings' : 'forward'}
                  </span>
                </div>

                {/* Event Card */}
                <div className="flex-1 bg-canvas-main/60 rounded-xl p-space-md border border-border-subtle/60 space-y-2.5 min-w-0">
                  {/* Top Bar: Kind Badge & Timestamp */}
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      {isReassign ? (
                        <span className="px-2 py-0.5 rounded-full bg-purple-500/15 text-purple-700 dark:text-purple-300 border border-purple-300/60 font-label-sm text-xs font-bold uppercase tracking-wider flex items-center gap-1">
                          <span className="material-symbols-outlined text-sm">admin_panel_settings</span>
                          Reassigned by owner
                        </span>
                      ) : (
                        <span className="px-2 py-0.5 rounded-full bg-primary/10 text-primary border border-primary/20 font-label-sm text-xs font-bold uppercase tracking-wider flex items-center gap-1">
                          <span className="material-symbols-outlined text-sm">forward</span>
                          Passed to colleague
                        </span>
                      )}

                      {h.status_before && h.status_after && (
                        <span className="text-[11px] font-mono text-text-muted">
                          {h.status_before} → {h.status_after}
                        </span>
                      )}
                    </div>

                    <time
                      dateTime={h.created_at}
                      className="font-mono text-xs text-text-muted"
                      title={new Date(h.created_at).toLocaleString()}
                    >
                      {new Date(h.created_at).toLocaleString([], {
                        month: 'short',
                        day: 'numeric',
                        hour: '2-digit',
                        minute: '2-digit',
                      })}
                    </time>
                  </div>

                  {/* Participants Row: From User -> To User */}
                  <div className="flex flex-wrap items-center gap-2 py-1 text-xs">
                    <div className="flex items-center gap-1.5">
                      <Avatar
                        src={h.from_user?.avatar_url}
                        name={fromName}
                        size="xs"
                        ring="ring-1 ring-border-subtle"
                      />
                      <span className="font-semibold text-text-primary">{fromName}</span>
                    </div>

                    <span className="material-symbols-outlined text-text-muted text-sm">trending_flat</span>

                    <div className="flex items-center gap-1.5">
                      <Avatar
                        src={h.to_user?.avatar_url}
                        name={toName}
                        size="xs"
                        ring="ring-1 ring-border-subtle"
                      />
                      <span className="font-semibold text-text-primary">{toName}</span>
                    </div>
                  </div>

                  {/* Message */}
                  {h.message && (
                    <div className="p-2.5 rounded-lg bg-surface-card border border-border-subtle/50 text-xs sm:text-body-sm text-text-primary whitespace-pre-wrap leading-relaxed">
                      {h.message}
                    </div>
                  )}

                  {/* Attachments Section */}
                  {hasAttachments && (
                    <div className="pt-1 space-y-1.5">
                      <span className="text-[11px] font-semibold text-text-secondary uppercase tracking-wider block">
                        Attached Files ({h.attachments.length})
                      </span>
                      <div className="flex flex-wrap gap-2">
                        {h.attachments.map((att) => {
                          const attName = att.original_name || att.name || att.filename || 'file';
                          const attSize = formatBytes(att.size_bytes || att.size || 0);
                          const iconName = getFileIcon(att);
                          const isDownloading = downloadingId === att.id;

                          return (
                            <button
                              key={att.id}
                              type="button"
                              onClick={() => handleDownloadAttachment(att)}
                              disabled={isDownloading}
                              className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-surface-card border border-border-subtle hover:border-primary/40 hover:bg-canvas-lavender/30 transition-all text-left text-xs group/att cursor-pointer"
                              title={`Download ${attName} (${attSize})`}
                              aria-label={`Download ${attName}`}
                            >
                              <span className="material-symbols-outlined text-sm text-text-muted group-hover/att:text-primary">
                                {iconName}
                              </span>
                              <span className="font-medium text-text-primary max-w-[140px] truncate">
                                {attName}
                              </span>
                              <span className="text-[10px] text-text-muted font-mono">
                                ({attSize})
                              </span>
                              <span className="material-symbols-outlined text-xs text-text-muted group-hover/att:text-primary ml-0.5">
                                {isDownloading ? 'hourglass_top' : 'download'}
                              </span>
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  )}
                </div>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
};

export default TicketHandoffTimeline;
