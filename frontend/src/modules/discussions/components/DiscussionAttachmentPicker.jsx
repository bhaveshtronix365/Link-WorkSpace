import React, { useState, useRef, useCallback, useEffect } from 'react';
import { discussionsService } from '../../../services/discussionsService';

const formatBytes = (bytes, decimals = 1) => {
  if (!bytes || bytes === 0) return '0 B';
  const k = 1024;
  const dm = decimals < 0 ? 0 : decimals;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(dm))} ${sizes[i]}`;
};

export const DiscussionAttachmentPicker = ({
  discussionId = 'new',
  onAttachmentIdsChange,
  onUploadingChange,
  maxFiles = 5,
  className = '',
}) => {
  const [pendingFiles, setPendingFiles] = useState([]);
  const [isDragging, setIsDragging] = useState(false);
  const [pickerError, setPickerError] = useState(null);
  const fileInputRef = useRef(null);
  const dropZoneRef = useRef(null);

  // Sync uploaded attachment IDs to parent
  useEffect(() => {
    const successIds = pendingFiles
      .filter((f) => f.status === 'success' && f.attachmentId)
      .map((f) => f.attachmentId);
    onAttachmentIdsChange?.(successIds);

    const isUploading = pendingFiles.some((f) => f.status === 'uploading');
    onUploadingChange?.(isUploading);
  }, [pendingFiles, onAttachmentIdsChange, onUploadingChange]);

  const uploadFile = useCallback(
    async (fileItem) => {
      setPendingFiles((prev) =>
        prev.map((f) =>
          f.id === fileItem.id
            ? { ...f, status: 'uploading', progress: 0, error: null }
            : f
        )
      );

      try {
        const res = await discussionsService.uploadAttachment(
          discussionId,
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
          err?.response?.data?.detail?.message ||
          err?.response?.data?.detail ||
          err?.response?.data?.error?.message ||
          err?.message ||
          'Upload failed';
        setPendingFiles((prev) =>
          prev.map((f) =>
            f.id === fileItem.id
              ? { ...f, status: 'error', progress: 0, error: errorMsg }
              : f
          )
        );
      }
    },
    [discussionId]
  );

  const handleFilesAdded = useCallback(
    (fileList) => {
      if (!fileList || fileList.length === 0) return;
      setPickerError(null);

      const files = Array.from(fileList);
      const currentValidCount = pendingFiles.filter((f) => f.status !== 'error').length;
      if (currentValidCount + files.length > maxFiles) {
        setPickerError(`You can attach a maximum of ${maxFiles} files per post.`);
        return;
      }

      const newItems = files.map((file) => ({
        id: `att_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
        file,
        name: file.name,
        size: file.size,
        status: 'uploading',
        progress: 0,
        attachmentId: null,
        error: null,
      }));

      setPendingFiles((prev) => [...prev, ...newItems]);
      newItems.forEach((item) => uploadFile(item));
    },
    [pendingFiles, maxFiles, uploadFile]
  );

  const handleRetry = (fileId) => {
    const item = pendingFiles.find((f) => f.id === fileId);
    if (item && item.file) {
      uploadFile(item);
    }
  };

  const handleRemove = (fileId) => {
    setPendingFiles((prev) => prev.filter((f) => f.id !== fileId));
  };

  // Drag and drop handlers
  const handleDragOver = (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (!isDragging) setIsDragging(true);
  };

  const handleDragLeave = (e) => {
    e.preventDefault();
    e.stopPropagation();
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

  const handlePaste = (e) => {
    const clipboardData = e.clipboardData;
    if (clipboardData && clipboardData.files && clipboardData.files.length > 0) {
      handleFilesAdded(clipboardData.files);
    }
  };

  return (
    <div
      ref={dropZoneRef}
      onDragOver={handleDragOver}
      onDragEnter={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      onPaste={handlePaste}
      className={`flex flex-col gap-2 ${className}`}
    >
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

      {/* Dragging Overlay */}
      {isDragging && (
        <div className="p-3 border-2 border-dashed border-primary rounded-xl bg-primary/5 text-primary text-center text-xs font-semibold animate-pulse flex items-center justify-center gap-2">
          <span className="material-symbols-outlined text-base">cloud_upload</span>
          <span>Drop files here to attach</span>
        </div>
      )}

      {/* Pending Files Chip Feed */}
      {pendingFiles.length > 0 && (
        <div className="flex flex-wrap gap-2 pt-1">
          {pendingFiles.map((f) => (
            <div
              key={f.id}
              className={`relative flex flex-col gap-1 p-2 rounded-lg border text-xs min-w-[160px] max-w-[260px] shadow-xs transition-all ${
                f.status === 'error'
                  ? 'border-red-300 bg-red-50/70 text-red-900'
                  : f.status === 'success'
                  ? 'border-border-subtle bg-canvas-main text-text-primary'
                  : 'border-primary/40 bg-primary/5 text-text-primary'
              }`}
            >
              <div className="flex items-center justify-between gap-1.5">
                <div className="flex items-center gap-1.5 min-w-0">
                  <span className="material-symbols-outlined text-[16px] text-text-muted shrink-0">
                    {f.name.endsWith('.pdf')
                      ? 'picture_as_pdf'
                      : /\.(png|jpe?g|webp|gif)$/i.test(f.name)
                      ? 'image'
                      : 'description'}
                  </span>
                  <span className="font-medium truncate text-xs" title={f.name}>
                    {f.name}
                  </span>
                </div>
                <button
                  type="button"
                  onClick={() => handleRemove(f.id)}
                  className="text-text-muted hover:text-text-primary p-0.5 rounded transition-colors shrink-0"
                  aria-label={`Remove ${f.name}`}
                >
                  <span className="material-symbols-outlined text-sm">close</span>
                </button>
              </div>

              <div className="flex items-center justify-between text-3xs text-text-muted">
                <span>{formatBytes(f.size)}</span>
                {f.status === 'uploading' && (
                  <span className="text-primary font-semibold">{f.progress}%</span>
                )}
                {f.status === 'success' && (
                  <span className="text-status-resolved flex items-center gap-0.5">
                    <span className="material-symbols-outlined text-3xs">check_circle</span> Ready
                  </span>
                )}
                {f.status === 'error' && (
                  <button
                    type="button"
                    onClick={() => handleRetry(f.id)}
                    className="text-red-700 underline font-semibold hover:text-red-900 flex items-center gap-0.5"
                  >
                    <span className="material-symbols-outlined text-3xs">refresh</span> Retry
                  </button>
                )}
              </div>

              {/* Progress Bar */}
              {f.status === 'uploading' && (
                <div className="w-full bg-border-subtle h-1 rounded-full overflow-hidden">
                  <div
                    className="bg-primary h-full transition-all duration-150"
                    style={{ width: `${f.progress}%` }}
                  />
                </div>
              )}

              {/* Error message */}
              {f.status === 'error' && f.error && (
                <div
                  className="text-red-600 text-3xs font-medium truncate"
                  role="alert"
                  aria-live="polite"
                  title={f.error}
                >
                  {f.error}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Trigger button & helper text */}
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          disabled={pendingFiles.filter((f) => f.status !== 'error').length >= maxFiles}
          className="px-2.5 py-1.5 rounded-lg text-text-secondary hover:text-text-primary hover:bg-canvas-lavender/80 transition-colors flex items-center gap-1.5 text-xs font-semibold disabled:opacity-50 cursor-pointer disabled:cursor-not-allowed border border-border-subtle"
          aria-label="Attach files"
        >
          <span className="material-symbols-outlined text-[18px]">attach_file</span>
          <span>Attach Files</span>
        </button>
        <span className="text-3xs text-text-muted">
          Images, PDFs, or logs up to 10 MB (max {maxFiles} files)
        </span>
      </div>

      {pickerError && (
        <div
          role="alert"
          aria-live="polite"
          className="p-2 bg-red-50 text-red-700 text-xs rounded-lg border border-red-200 flex items-center justify-between"
        >
          <span>{pickerError}</span>
          <button
            type="button"
            onClick={() => setPickerError(null)}
            className="text-red-500 hover:text-red-700 p-0.5"
          >
            <span className="material-symbols-outlined text-sm">close</span>
          </button>
        </div>
      )}
    </div>
  );
};

export default DiscussionAttachmentPicker;
