import React, { useState, useEffect, useCallback, useRef } from 'react';
import { ticketsService, createTicketsWebSocket } from '../../../services/ticketsService';
import { authService } from '../../../services/api';
import Modal from '../../../components/common/Modal';

const ALLOWED_EXTENSIONS = ['.png', '.jpg', '.jpeg', '.webp', '.gif', '.pdf', '.txt', '.log', '.csv', '.json'];
const MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024; // 10 MB

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

export const TicketAttachmentsCard = ({ ticketId, initialAttachments = [], onAttachmentChange }) => {
  const [attachments, setAttachments] = useState(initialAttachments);
  const [loading, setLoading] = useState(false);
  const [fetchError, setFetchError] = useState(null);

  // Uploading state
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [uploadFileName, setUploadFileName] = useState('');
  const [uploadError, setUploadError] = useState(null);

  // In-flight actions
  const [downloadingId, setDownloadingId] = useState(null);
  const [deletingId, setDeletingId] = useState(null);
  const [confirmDeleteFile, setConfirmDeleteFile] = useState(null);
  const [statusMessage, setStatusMessage] = useState('');

  const fileInputRef = useRef(null);

  // Current authenticated user for permission checks
  const currentUser = authService.getCurrentUserSync();

  const canDelete = useCallback(
    (file) => {
      if (!currentUser || !currentUser.id) return false;
      if (currentUser.id === file.uploader_id || currentUser.id === file.uploader?.id) return true;
      if (currentUser.role === 'owner' || currentUser.role === 'admin') return true;
      if (Array.isArray(currentUser.memberships)) {
        return currentUser.memberships.some(
          (m) => (m.role === 'owner' || m.role === 'admin') && m.status === 'active'
        );
      }
      return false;
    },
    [currentUser]
  );

  // Fetch attachments from API
  const loadAttachments = useCallback(async () => {
    if (!ticketId) return;
    try {
      setLoading(true);
      setFetchError(null);
      const data = await ticketsService.getAttachments(ticketId);
      // Sort newest first
      const sorted = (Array.isArray(data) ? data : []).sort(
        (a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0)
      );
      setAttachments(sorted);
    } catch (err) {
      console.error('Failed to load attachments:', err);
      setFetchError('Failed to load attachments. Please retry.');
    } finally {
      setLoading(false);
    }
  }, [ticketId]);

  // Initial load if not provided or to ensure complete data
  useEffect(() => {
    loadAttachments();
  }, [loadAttachments]);

  // WebSocket Live Updates
  useEffect(() => {
    if (!ticketId) return;
    const wsFn = createTicketsWebSocket || ticketsService?.createTicketsWebSocket;
    let cleanup = null;
    if (typeof wsFn === 'function') {
      const orgId =
        currentUser?.organization_id ||
        localStorage.getItem('organization_id') ||
        localStorage.getItem('active_workspace_id') ||
        '';

      cleanup = wsFn(orgId, (event) => {
        const eventType = event?.type || event?.event;
        const eventTicketId = event?.ticket_id || event?.ticket?.id || event?.data?.ticket_id;

        if (
          eventTicketId === ticketId &&
          (eventType === 'ticket_attachment_changed' ||
            eventType === 'attachment_added' ||
            eventType === 'attachment_deleted' ||
            eventType === 'comment_added' ||
            eventType === 'ticket_comment_added' ||
            eventType === 'ticket_updated')
        ) {
          loadAttachments();
          if (typeof onAttachmentChange === 'function') {
            onAttachmentChange();
          }
        }
      });
    }

    return () => {
      if (typeof cleanup === 'function') cleanup();
      else if (cleanup && typeof cleanup.close === 'function') cleanup.close();
    };
  }, [ticketId, currentUser, loadAttachments, onAttachmentChange]);

  // Handle Download
  const handleDownload = async (file) => {
    const fileName = file.original_name || file.name || file.filename || 'attachment';
    try {
      setDownloadingId(file.id);
      setStatusMessage(`Downloading ${fileName}...`);
      await ticketsService.downloadAttachment(ticketId, file.id, fileName);
      setStatusMessage(`Downloaded ${fileName}`);
    } catch (err) {
      console.error('Download error:', err);
      setStatusMessage(`Failed to download ${fileName}`);
    } finally {
      setDownloadingId(null);
    }
  };

  // Handle Delete Confirmation
  const handleConfirmDelete = async () => {
    if (!confirmDeleteFile) return;
    const file = confirmDeleteFile;
    const fileName = file.original_name || file.name || file.filename || 'attachment';

    try {
      setDeletingId(file.id);
      setConfirmDeleteFile(null);
      setStatusMessage(`Deleting ${fileName}...`);
      await ticketsService.deleteAttachment(ticketId, file.id);
      setAttachments((prev) => prev.filter((a) => a.id !== file.id));
      setStatusMessage(`Successfully deleted ${fileName}`);
      if (typeof onAttachmentChange === 'function') {
        onAttachmentChange();
      }
    } catch (err) {
      console.error('Failed to delete attachment:', err);
      const errMsg = err.response?.data?.error?.message || err.response?.data?.detail || 'Failed to delete attachment.';
      setUploadError(typeof errMsg === 'object' ? errMsg.message : errMsg);
      setStatusMessage(`Failed to delete ${fileName}`);
    } finally {
      setDeletingId(null);
    }
  };

  // Direct Diagnostic File Upload
  const handleFileSelect = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;

    // Reset input so re-selecting same file triggers change
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }

    setUploadError(null);

    // Validation: Allowed extension
    const ext = `.${(file.name.split('.').pop() || '').toLowerCase()}`;
    if (!ALLOWED_EXTENSIONS.includes(ext)) {
      setUploadError(`File type "${ext}" is not permitted. Allowed: ${ALLOWED_EXTENSIONS.join(', ')}`);
      return;
    }

    // Validation: File size
    if (file.size > MAX_FILE_SIZE_BYTES) {
      setUploadError(
        `File size (${formatBytes(file.size)}) exceeds the maximum limit of ${formatBytes(MAX_FILE_SIZE_BYTES)}.`
      );
      return;
    }

    setUploading(true);
    setUploadProgress(0);
    setUploadFileName(file.name);
    setStatusMessage(`Uploading ${file.name}...`);

    try {
      const newAtt = await ticketsService.addAttachment(ticketId, file, (progressEvent) => {
        if (progressEvent.total) {
          const percent = Math.round((progressEvent.loaded * 100) / progressEvent.total);
          setUploadProgress(percent);
        }
      });

      // Insert at the beginning (newest first)
      setAttachments((prev) => [newAtt, ...prev.filter((a) => a.id !== newAtt.id)]);
      setStatusMessage(`Successfully uploaded ${file.name}`);
      if (typeof onAttachmentChange === 'function') {
        onAttachmentChange();
      }
    } catch (err) {
      console.error('Upload error:', err);
      const errMsg = err.response?.data?.error?.message || err.response?.data?.detail || 'Failed to upload diagnostic file.';
      setUploadError(typeof errMsg === 'object' ? errMsg.message : errMsg);
      setStatusMessage(`Failed to upload ${file.name}`);
    } finally {
      setUploading(false);
      setUploadProgress(0);
      setUploadFileName('');
    }
  };

  // Scroll smoothly to source comment
  const scrollToComment = (commentId) => {
    const target = document.getElementById(`comment-${commentId}`);
    if (target) {
      target.scrollIntoView({ behavior: 'smooth', block: 'center' });
      target.classList.add('ring-2', 'ring-primary', 'ring-offset-2');
      setTimeout(() => {
        target.classList.remove('ring-2', 'ring-primary', 'ring-offset-2');
      }, 2500);
      target.focus?.();
    }
  };

  return (
    <div
      className="bg-surface-card rounded-xl p-space-lg shadow-sm space-y-space-md"
      role="region"
      aria-label="Attachments & Artifacts"
    >
      {/* Live Region for Screen Readers */}
      <div className="sr-only" aria-live="polite" aria-atomic="true">
        {statusMessage}
      </div>

      {/* Card Header with Real Count */}
      <div className="flex items-center justify-between border-b border-border-subtle pb-space-sm">
        <div className="flex items-center gap-2">
          <span className="material-symbols-outlined text-primary text-xl">folder_zip</span>
          <h2 className="font-headline-sm text-headline-sm text-text-primary">Attachments &amp; Artifacts</h2>
        </div>
        <span
          className="px-2.5 py-0.5 rounded-full bg-canvas-lavender text-text-secondary font-label-sm text-label-sm font-semibold"
          aria-label={`${attachments.length} attached files`}
        >
          {attachments.length} {attachments.length === 1 ? 'file' : 'files'}
        </span>
      </div>

      {/* Error state with retry */}
      {fetchError && (
        <div
          className="p-space-md bg-red-50 border border-red-200 text-red-700 text-xs rounded-xl flex items-center justify-between gap-2"
          role="alert"
        >
          <div className="flex items-center gap-2 min-w-0">
            <span className="material-symbols-outlined text-base shrink-0">error</span>
            <span className="truncate">{fetchError}</span>
          </div>
          <button
            type="button"
            onClick={loadAttachments}
            className="px-2.5 py-1 bg-red-100 hover:bg-red-200 text-red-800 rounded-md font-semibold text-xs transition-colors shrink-0"
          >
            Retry
          </button>
        </div>
      )}

      {/* Upload Error Banner */}
      {uploadError && (
        <div
          className="p-2.5 bg-red-50 border border-red-200 text-red-700 text-xs rounded-lg flex items-center justify-between gap-2"
          role="alert"
        >
          <div className="flex items-center gap-1.5 min-w-0">
            <span className="material-symbols-outlined text-sm shrink-0">error_outline</span>
            <span className="truncate">{uploadError}</span>
          </div>
          <button
            type="button"
            onClick={() => setUploadError(null)}
            className="text-red-500 hover:text-red-700 p-0.5 rounded focus:outline-none focus:ring-1 focus:ring-red-400"
            aria-label="Dismiss error"
          >
            <span className="material-symbols-outlined text-sm">close</span>
          </button>
        </div>
      )}

      {/* In-Flight Upload Progress */}
      {uploading && (
        <div
          className="p-3 bg-canvas-lavender/50 rounded-xl border border-primary/20 space-y-2"
          aria-live="polite"
        >
          <div className="flex items-center justify-between text-xs">
            <span className="font-medium text-text-primary truncate max-w-[200px]" title={uploadFileName}>
              {uploadFileName}
            </span>
            <span className="font-mono text-primary font-bold">{uploadProgress}%</span>
          </div>
          <div
            className="w-full bg-surface-card rounded-full h-1.5 overflow-hidden"
            role="progressbar"
            aria-valuenow={uploadProgress}
            aria-valuemin="0"
            aria-valuemax="100"
          >
            <div
              className="bg-primary h-full transition-all duration-200 rounded-full"
              style={{ width: `${uploadProgress}%` }}
            ></div>
          </div>
        </div>
      )}

      {/* Loading State */}
      {loading && attachments.length === 0 ? (
        <div className="py-8 flex flex-col items-center justify-center text-text-muted space-y-2">
          <span className="material-symbols-outlined text-2xl animate-spin text-primary">sync</span>
          <span className="text-xs font-medium">Loading attachments...</span>
        </div>
      ) : attachments.length === 0 ? (
        /* Empty State */
        <div className="p-space-lg text-center bg-canvas-lavender/30 rounded-xl border border-dashed border-border-subtle space-y-1">
          <span className="material-symbols-outlined text-3xl text-text-muted">folder_open</span>
          <p className="font-label-md text-text-secondary text-sm">No diagnostic files or artifacts</p>
          <p className="font-body-sm text-text-muted text-xs">
            Upload files directly or attach them to comments to track triage artifacts.
          </p>
        </div>
      ) : (
        /* Attachments List (Newest First) */
        <div className="space-y-space-sm max-h-[380px] overflow-y-auto pr-1">
          {attachments.map((file) => {
            const fileName = file.original_name || file.name || file.filename || 'attachment';
            const iconName = getFileIcon(file);
            const isDeleting = deletingId === file.id;
            const isDownloading = downloadingId === file.id;
            const hasDeletePermission = canDelete(file);

            return (
              <div
                key={file.id}
                className="p-space-sm rounded-xl bg-canvas-lavender/50 hover:bg-canvas-lavender flex items-center justify-between gap-3 transition-colors group border border-transparent hover:border-border-subtle"
              >
                {/* File Info */}
                <div className="flex items-center gap-3 min-w-0 flex-1">
                  <div className="w-10 h-10 rounded-lg bg-surface-card text-primary flex items-center justify-center shadow-xs shrink-0 border border-border-subtle">
                    <span className="material-symbols-outlined text-xl">{iconName}</span>
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="font-label-md text-label-md text-text-primary truncate font-medium" title={fileName}>
                      {fileName}
                    </p>
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-3xs text-text-secondary mt-0.5">
                      <span className="font-mono font-semibold">{formatBytes(file.size_bytes)}</span>
                      <span>•</span>
                      <span className="truncate max-w-[120px]" title={file.uploader?.full_name || file.uploader?.email || 'Unknown'}>
                        {file.uploader?.full_name || file.uploader?.email || 'Unknown'}
                      </span>
                      <span>•</span>
                      <span>
                        {file.created_at
                          ? new Date(file.created_at).toLocaleString([], {
                              dateStyle: 'short',
                              timeStyle: 'short',
                            })
                          : 'Just now'}
                      </span>
                    </div>

                    {/* Source: Comment link or direct upload badge */}
                    <div className="mt-1">
                      {file.comment_id ? (
                        <button
                          type="button"
                          onClick={() => scrollToComment(file.comment_id)}
                          className="inline-flex items-center gap-1 text-3xs text-primary hover:text-primary-hover hover:underline font-medium focus:outline-none focus:ring-1 focus:ring-primary rounded"
                          title="Jump to associated comment in feed"
                          aria-label={`Jump to associated comment for ${fileName}`}
                        >
                          <span className="material-symbols-outlined text-xs">forum</span>
                          <span>From comment</span>
                        </button>
                      ) : (
                        <span className="inline-flex items-center gap-1 text-3xs text-text-muted bg-surface-card/80 px-2 py-0.5 rounded-full font-medium border border-border-subtle">
                          <span className="material-symbols-outlined text-xs">upload_file</span>
                          Direct upload
                        </span>
                      )}
                    </div>
                  </div>
                </div>

                {/* Actions: Download (for all authorized viewers) & Delete (uploader / admin only) */}
                <div className="flex items-center gap-1 shrink-0">
                  <button
                    type="button"
                    onClick={() => handleDownload(file)}
                    disabled={isDownloading}
                    className="p-1.5 rounded-lg text-text-secondary hover:text-primary hover:bg-surface-card transition-colors focus:outline-none focus:ring-1 focus:ring-primary disabled:opacity-50"
                    title={`Download ${fileName}`}
                    aria-label={`Download ${fileName}`}
                  >
                    <span className="material-symbols-outlined text-lg">
                      {isDownloading ? 'hourglass_top' : 'download'}
                    </span>
                  </button>

                  {hasDeletePermission && (
                    <button
                      type="button"
                      onClick={() => setConfirmDeleteFile(file)}
                      disabled={isDeleting}
                      className="p-1.5 rounded-lg text-text-muted hover:text-red-600 hover:bg-red-50 transition-colors focus:outline-none focus:ring-1 focus:ring-red-400 disabled:opacity-50"
                      title={`Delete ${fileName}`}
                      aria-label={`Delete ${fileName}`}
                    >
                      <span className="material-symbols-outlined text-lg">
                        {isDeleting ? 'hourglass_empty' : 'delete'}
                      </span>
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Hidden File Input for "Upload Diagnostic File" */}
      <input
        type="file"
        ref={fileInputRef}
        onChange={handleFileSelect}
        accept={ALLOWED_EXTENSIONS.join(',')}
        className="hidden"
        aria-hidden="true"
      />

      {/* "Upload Diagnostic File" Button */}
      <button
        type="button"
        onClick={() => fileInputRef.current?.click()}
        disabled={uploading}
        className="w-full py-2.5 rounded-lg bg-surface-card hover:bg-canvas-lavender active:bg-canvas-lavender/80 text-primary font-label-md text-label-md font-semibold transition-colors flex items-center justify-center gap-2 shadow-xs border border-border-subtle focus:outline-none focus:ring-2 focus:ring-primary disabled:opacity-50 cursor-pointer"
        aria-label="Upload Diagnostic File"
      >
        <span className="material-symbols-outlined text-lg">cloud_upload</span>
        <span>{uploading ? 'Uploading Diagnostic File...' : 'Upload Diagnostic File'}</span>
      </button>

      {/* Confirmation Modal for Delete */}
      {confirmDeleteFile && (
        <Modal
          isOpen={Boolean(confirmDeleteFile)}
          onClose={() => setConfirmDeleteFile(null)}
          title="Delete Attachment"
        >
          <div className="space-y-4">
            <p className="font-body-md text-text-secondary">
              Are you sure you want to permanently delete{' '}
              <strong className="text-text-primary">
                {confirmDeleteFile.original_name || confirmDeleteFile.name || confirmDeleteFile.filename}
              </strong>
              ? This action cannot be undone and will remove the file from storage.
            </p>
            <div className="flex justify-end gap-3 pt-2">
              <button
                type="button"
                onClick={() => setConfirmDeleteFile(null)}
                className="px-4 py-2 rounded-lg bg-surface-card hover:bg-canvas-lavender text-text-secondary font-label-md text-label-md border border-border-subtle transition-colors focus:outline-none focus:ring-1 focus:ring-border-subtle"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleConfirmDelete}
                className="px-4 py-2 rounded-lg bg-red-600 hover:bg-red-700 text-white font-label-md text-label-md font-semibold shadow-xs transition-colors focus:outline-none focus:ring-2 focus:ring-red-500"
              >
                Delete File
              </button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
};

export default TicketAttachmentsCard;
