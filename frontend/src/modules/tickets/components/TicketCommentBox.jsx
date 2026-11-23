import React, { useState, useRef, useCallback } from 'react';
import { ticketsService } from '../../../services/ticketsService';

const formatBytes = (bytes, decimals = 1) => {
  if (!bytes || bytes === 0) return '0 B';
  const k = 1024;
  const dm = decimals < 0 ? 0 : decimals;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(dm))} ${sizes[i]}`;
};

export const TicketCommentBox = ({ ticketId, onCommentAdded }) => {
  const [content, setContent] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const [pendingFiles, setPendingFiles] = useState([]);
  const [formError, setFormError] = useState(null);

  const fileInputRef = useRef(null);
  const dropZoneRef = useRef(null);

  // Upload a single file with progress tracking
  const uploadSingleFile = useCallback(async (fileItem) => {
    setPendingFiles((prev) =>
      prev.map((f) =>
        f.id === fileItem.id
          ? { ...f, status: 'uploading', progress: 0, error: null }
          : f
      )
    );

    try {
      const res = await ticketsService.addAttachment(
        ticketId,
        fileItem.file,
        (progressEvent) => {
          if (progressEvent.total) {
            const pct = Math.round((progressEvent.loaded * 100) / progressEvent.total);
            setPendingFiles((prev) =>
              prev.map((f) =>
                f.id === fileItem.id ? { ...f, progress: pct } : f
              )
            );
          }
        }
      );

      setPendingFiles((prev) =>
        prev.map((f) =>
          f.id === fileItem.id
            ? { ...f, status: 'success', progress: 100, attachmentId: res.id }
            : f
        )
      );
    } catch (err) {
      const errorMsg =
        err.response?.data?.error?.message ||
        err.response?.data?.detail?.message ||
        err.response?.data?.detail ||
        err.message ||
        'Failed to upload file.';
      setPendingFiles((prev) =>
        prev.map((f) =>
          f.id === fileItem.id
            ? { ...f, status: 'error', progress: 0, error: errorMsg }
            : f
        )
      );
    }
  }, [ticketId]);

  // Handle incoming File list (from input, drop, or paste)
  const handleFilesAdded = useCallback((fileList) => {
    if (!fileList || fileList.length === 0) return;
    setFormError(null);

    const newItems = Array.from(fileList).map((file) => ({
      id: `pending_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`,
      file,
      name: file.name,
      size: file.size,
      status: 'uploading',
      progress: 0,
      attachmentId: null,
      error: null,
    }));

    setPendingFiles((prev) => [...prev, ...newItems]);

    // Kick off upload for each file immediately
    newItems.forEach((item) => {
      uploadSingleFile(item);
    });
  }, [uploadSingleFile]);

  // Retry upload for a failed file
  const handleRetry = (fileId) => {
    const item = pendingFiles.find((f) => f.id === fileId);
    if (item && item.file) {
      uploadSingleFile(item);
    }
  };

  // Remove a pending file chip
  const handleRemove = (fileId) => {
    setPendingFiles((prev) => prev.filter((f) => f.id !== fileId));
  };

  // Drag and Drop handlers
  const handleDragOver = (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (!isDragging) setIsDragging(true);
  };

  const handleDragEnter = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(true);
  };

  const handleDragLeave = (e) => {
    e.preventDefault();
    e.stopPropagation();
    // Only deactivate if leaving the container itself
    if (dropZoneRef.current && !dropZoneRef.current.contains(e.relatedTarget)) {
      setIsDragging(false);
    }
  };

  const handleDrop = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
    if (e.dataTransfer && e.dataTransfer.files) {
      handleFilesAdded(e.dataTransfer.files);
    }
  };

  // Paste handler (clipboard image / files)
  const handlePaste = (e) => {
    const clipboardData = e.clipboardData;
    if (!clipboardData) return;

    // Check for files first
    if (clipboardData.files && clipboardData.files.length > 0) {
      handleFilesAdded(clipboardData.files);
      return;
    }

    // Check items for images
    const items = clipboardData.items;
    if (items && items.length > 0) {
      const pastedFiles = [];
      for (let i = 0; i < items.length; i++) {
        const item = items[i];
        if (item.kind === 'file' || item.type.startsWith('image/')) {
          const file = item.getAsFile();
          if (file) {
            // Generate a sensible name for pasted image
            const ext = file.type.split('/')[1] || 'png';
            const renamedFile = new File([file], `pasted_image_${Date.now()}.${ext}`, {
              type: file.type,
            });
            pastedFiles.push(renamedFile);
          }
        }
      }
      if (pastedFiles.length > 0) {
        handleFilesAdded(pastedFiles);
      }
    }
  };

  // Form submission
  const handleSubmit = async (e) => {
    e.preventDefault();
    setFormError(null);

    const isAnyUploading = pendingFiles.some((f) => f.status === 'uploading');
    if (isAnyUploading || submitting) return;

    const trimmedContent = content.trim();
    const successfulAttachmentIds = pendingFiles
      .filter((f) => f.status === 'success' && f.attachmentId)
      .map((f) => f.attachmentId);

    // Validate: comment cannot be completely empty (must have text or at least one uploaded attachment)
    if (!trimmedContent && successfulAttachmentIds.length === 0) {
      setFormError('Please enter a comment or attach at least one file.');
      return;
    }

    try {
      setSubmitting(true);
      const payload = {
        content: trimmedContent || undefined,
        attachment_ids: successfulAttachmentIds.length > 0 ? successfulAttachmentIds : undefined,
      };

      await ticketsService.addComment(ticketId, payload);
      setContent('');
      setPendingFiles([]);
      if (typeof onCommentAdded === 'function') {
        onCommentAdded();
      }
    } catch (err) {
      const msg =
        err.response?.data?.error?.message ||
        err.response?.data?.detail?.message ||
        err.response?.data?.detail ||
        err.message ||
        'Failed to post comment.';
      setFormError(msg);
    } finally {
      setSubmitting(false);
    }
  };

  // State checks for submit button disabled state
  const isAnyUploading = pendingFiles.some((f) => f.status === 'uploading');
  const hasUploadedFiles = pendingFiles.some((f) => f.status === 'success' && f.attachmentId);
  const hasText = Boolean(content.trim());
  const isSubmitDisabled = submitting || isAnyUploading || (!hasText && !hasUploadedFiles);

  return (
    <form
      ref={dropZoneRef}
      onSubmit={handleSubmit}
      onDragOver={handleDragOver}
      onDragEnter={handleDragEnter}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      className={`relative p-space-md bg-canvas-lavender/60 rounded-xl space-y-space-sm transition-all duration-200 border-2 ${
        isDragging
          ? 'border-dashed border-primary bg-primary/5 shadow-inner'
          : 'border-transparent'
      }`}
      aria-label="Comment composer"
    >
      {/* Drag & Drop Visual Overlay */}
      {isDragging && (
        <div className="absolute inset-0 z-10 bg-surface-card/90 backdrop-blur-xs rounded-xl flex flex-col items-center justify-center pointer-events-none border-2 border-dashed border-primary">
          <span className="material-symbols-outlined text-primary text-4xl mb-2 animate-bounce">
            cloud_upload
          </span>
          <span className="font-headline-sm text-headline-sm text-primary font-semibold">
            Drop files here to attach
          </span>
          <span className="text-xs text-text-muted mt-1">
            PNG, JPG, WebP, GIF, PDF, TXT, LOG, CSV, JSON (up to 10 MB each)
          </span>
        </div>
      )}

      {/* Header */}
      <div className="flex items-center justify-between">
        <label htmlFor={`comment-textarea-${ticketId}`} className="font-label-md text-label-md text-text-primary font-semibold">
          Post Reply or Operational Update
        </label>
        {isAnyUploading && (
          <span className="text-xs text-primary font-medium flex items-center gap-1 animate-pulse">
            <span className="material-symbols-outlined text-sm animate-spin">sync</span>
            Uploading attachments...
          </span>
        )}
      </div>

      {/* Composer Card */}
      <div className="bg-surface-card rounded-lg shadow-sm overflow-hidden border border-border-subtle focus-within:border-primary focus-within:ring-1 focus-within:ring-primary/20 transition-all">
        <textarea
          id={`comment-textarea-${ticketId}`}
          rows={3}
          value={content}
          onChange={(e) => setContent(e.target.value)}
          onPaste={handlePaste}
          placeholder="Type your comment, update steps, or notes (paste images or drag files directly)..."
          className="w-full p-3 font-body-md text-body-md text-text-primary placeholder:text-text-muted outline-none resize-y bg-transparent"
        />

        {/* Pending File Chips List */}
        {pendingFiles.length > 0 && (
          <div className="p-2.5 bg-canvas-lavender/20 border-t border-border-subtle space-y-2">
            <div className="flex items-center justify-between text-xs text-text-secondary font-medium">
              <span>Attachments ({pendingFiles.length}/5)</span>
              <span className="text-text-muted">Optional</span>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2" role="region" aria-label="Attached files list">
              {pendingFiles.map((f) => (
                <div
                  key={f.id}
                  className="flex flex-col p-2 bg-surface-card rounded-lg border border-border-subtle shadow-2xs space-y-1.5 transition-all"
                >
                  <div className="flex items-center justify-between gap-1.5">
                    <div className="flex items-center gap-1.5 min-w-0">
                      <span className="material-symbols-outlined text-text-secondary text-base shrink-0">
                        {f.file?.type?.startsWith('image/') ? 'image' : 'description'}
                      </span>
                      <span className="font-label-sm text-label-sm text-text-primary truncate font-medium" title={f.name}>
                        {f.name}
                      </span>
                      <span className="text-text-muted text-3xs shrink-0">
                        ({formatBytes(f.size)})
                      </span>
                    </div>

                    <div className="flex items-center gap-1 shrink-0">
                      {f.status === 'success' && (
                        <span className="material-symbols-outlined text-green-600 text-base" title="Uploaded successfully">
                          check_circle
                        </span>
                      )}
                      {f.status === 'uploading' && (
                        <span className="text-3xs text-primary font-bold">
                          {f.progress}%
                        </span>
                      )}
                      {f.status === 'error' && (
                        <button
                          type="button"
                          onClick={() => handleRetry(f.id)}
                          className="px-1.5 py-0.5 rounded bg-red-100 hover:bg-red-200 text-red-700 text-3xs font-semibold transition-colors flex items-center gap-0.5"
                          aria-label={`Retry uploading ${f.name}`}
                        >
                          <span className="material-symbols-outlined text-3xs">replay</span>
                          <span>Retry</span>
                        </button>
                      )}
                      <button
                        type="button"
                        onClick={() => handleRemove(f.id)}
                        className="text-text-muted hover:text-red-600 p-0.5 rounded transition-colors focus:outline-none focus:ring-1 focus:ring-red-400"
                        aria-label={`Remove ${f.name}`}
                      >
                        <span className="material-symbols-outlined text-base">close</span>
                      </button>
                    </div>
                  </div>

                  {/* Progress Bar */}
                  {f.status === 'uploading' && (
                    <div
                      className="w-full bg-border-subtle h-1.5 rounded-full overflow-hidden"
                      role="progressbar"
                      aria-valuenow={f.progress}
                      aria-valuemin="0"
                      aria-valuemax="100"
                      aria-label={`Uploading ${f.name}`}
                    >
                      <div
                        className="bg-primary h-full transition-all duration-150 ease-out"
                        style={{ width: `${f.progress}%` }}
                      />
                    </div>
                  )}

                  {/* Error Notification */}
                  {f.status === 'error' && (
                    <div
                      className="text-red-600 text-3xs font-medium flex items-center gap-1"
                      role="alert"
                      aria-live="polite"
                    >
                      <span className="material-symbols-outlined text-3xs">warning</span>
                      <span className="truncate">{f.error}</span>
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Toolbar & Footer */}
        <div className="px-3 py-2 bg-canvas-lavender/30 flex items-center justify-between border-t border-border-subtle">
          <div className="flex items-center gap-2">
            {/* Paperclip Button */}
            <input
              type="file"
              ref={fileInputRef}
              onChange={(e) => {
                handleFilesAdded(e.target.files);
                e.target.value = '';
              }}
              multiple
              accept=".png,.jpg,.jpeg,.webp,.gif,.pdf,.txt,.log,.csv,.json"
              className="hidden"
              aria-hidden="true"
              tabIndex={-1}
            />
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              className="px-2.5 py-1.5 rounded-lg text-text-secondary hover:text-text-primary hover:bg-canvas-lavender/80 transition-colors flex items-center gap-1.5 text-xs font-semibold focus:outline-none focus:ring-2 focus:ring-primary/40"
              aria-label="Attach files to comment"
            >
              <span className="material-symbols-outlined text-base">attach_file</span>
              <span>Attach</span>
            </button>
            <span className="hidden sm:inline-block text-xs text-text-muted">
              (Drag &amp; drop or paste images supported)
            </span>
          </div>

          <button
            type="submit"
            disabled={isSubmitDisabled}
            className="px-4 py-1.5 rounded-lg bg-btn-primary hover:bg-primary-hover active:bg-primary disabled:opacity-50 text-on-primary font-label-sm text-label-sm font-semibold shadow-sm transition-colors flex items-center gap-1.5 focus:outline-none focus:ring-2 focus:ring-primary/40 cursor-pointer disabled:cursor-not-allowed"
          >
            <span>{submitting ? 'Posting...' : 'Post Comment'}</span>
            <span className="material-symbols-outlined text-sm">send</span>
          </button>
        </div>
      </div>

      {/* Form Error Announcement */}
      {formError && (
        <div
          role="alert"
          aria-live="polite"
          className="p-2.5 bg-red-50 text-red-700 text-xs rounded-lg border border-red-200 flex items-center justify-between"
        >
          <div className="flex items-center gap-1.5">
            <span className="material-symbols-outlined text-sm text-red-500">error</span>
            <span>{formError}</span>
          </div>
          <button
            type="button"
            onClick={() => setFormError(null)}
            className="text-red-500 hover:text-red-700 p-0.5 rounded"
            aria-label="Dismiss error"
          >
            <span className="material-symbols-outlined text-sm">close</span>
          </button>
        </div>
      )}
    </form>
  );
};
