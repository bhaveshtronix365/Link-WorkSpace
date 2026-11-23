import React, { useState, useEffect, useRef } from 'react';
import { taskService } from '../services/taskService';
import Avatar from '../../../components/common/Avatar';
import Modal from '../../../components/common/Modal';

/**
 * Format bytes into human-readable size
 */
const formatBytes = (bytes, decimals = 1) => {
  if (!bytes || bytes === 0) return '0 B';
  const k = 1024;
  const dm = decimals < 0 ? 0 : decimals;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(dm))} ${sizes[i]}`;
};

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
 * CommentComposerAndAttachmentPicker (Prompt 35)
 * Combines comment composer with formatting tools and file attachment upload & management.
 */
export const CommentComposerAndAttachmentPicker = ({
  taskId,
  onCommentAdded,
  onAttachmentAdded,
  onAttachmentDeleted,
  currentTab,
  hideTabBar = false,
  onCountsChange,
}) => {
  const [commentBody, setCommentBody] = useState('');
  const [comments, setComments] = useState([]);
  const [attachments, setAttachments] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isPosting, setIsPosting] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const [internalTab, setInternalTab] = useState('comments'); // 'comments' | 'attachments'
  const [deletingAttachment, setDeletingAttachment] = useState(null);
  const [isDeleting, setIsDeleting] = useState(false);

  const activeTab = currentTab || internalTab;

  const fileInputRef = useRef(null);

  const getAuthorName = (c) => {
    if (c.author?.display_name) return c.author.display_name;
    if (c.author?.full_name) return c.author.full_name;
    if (c.author_name) return c.author_name;
    return 'Deleted user';
  };

  const getAuthorRole = (c) => {
    return c.author?.role || c.author_role || null;
  };

  const getAuthorAvatar = (c) => {
    return c.author?.avatar_url || c.author_avatar || null;
  };

  // Load comments and attachments strictly from API
  useEffect(() => {
    if (!taskId) return;

    let isMounted = true;
    setIsLoading(true);

    Promise.all([
      taskService.listComments(taskId),
      taskService.listAttachments(taskId),
    ])
      .then(([loadedComments, loadedAttachments]) => {
        if (isMounted) {
          const coms = loadedComments || [];
          const atts = loadedAttachments || [];
          setComments(coms);
          setAttachments(atts);
          onCountsChange?.({ commentsCount: coms.length, attachmentsCount: atts.length });
        }
      })
      .finally(() => {
        if (isMounted) setIsLoading(false);
      });

    return () => {
      isMounted = false;
    };
  }, [taskId]);

  const handlePostComment = async (e) => {
    e?.preventDefault();
    if (!commentBody.trim() || isPosting) return;

    setIsPosting(true);
    try {
      const newComment = await taskService.addComment(taskId, commentBody.trim());
      setComments((prev) => [...prev, newComment]);
      setCommentBody(''); // Resets to empty
      if (onCommentAdded) onCommentAdded(newComment);
    } catch (err) {
      console.error('Failed to post comment', err);
    } finally {
      setIsPosting(false);
    }
  };

  const handleFileSelect = async (files) => {
    if (!files || files.length === 0) return;
    setIsUploading(true);

    try {
      for (const file of Array.from(files)) {
        const metadata = {
          filename: file.name,
          file_size: file.size,
          mime_type: file.type || 'application/octet-stream',
          storage_key: `tasks/${taskId}/${Date.now()}-${file.name}`,
        };
        const savedAttachment = await taskService.registerAttachment(taskId, metadata);
        setAttachments((prev) => [...prev, savedAttachment]);
        if (onAttachmentAdded) onAttachmentAdded(savedAttachment);
      }
    } catch (err) {
      console.error('Failed to attach file', err);
    } finally {
      setIsUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const handleDragOver = (e) => {
    e.preventDefault();
    setIsDragging(true);
  };

  const handleDragLeave = () => {
    setIsDragging(false);
  };

  const handleDrop = (e) => {
    e.preventDefault();
    setIsDragging(false);
    if (e.dataTransfer.files) {
      handleFileSelect(e.dataTransfer.files);
    }
  };

  const handleDeleteAttachment = async () => {
    if (!deletingAttachment) return;
    setIsDeleting(true);
    try {
      await taskService.deleteAttachment(taskId, deletingAttachment.id);
      setAttachments((prev) => {
        const next = prev.filter((a) => a.id !== deletingAttachment.id);
        onCountsChange?.({ commentsCount: comments.length, attachmentsCount: next.length });
        return next;
      });
      if (onAttachmentDeleted) onAttachmentDeleted(deletingAttachment);
      setDeletingAttachment(null);
    } catch (err) {
      console.error('Failed to delete attachment', err);
    } finally {
      setIsDeleting(false);
    }
  };

  return (
    <div className="space-y-4">
      {/* Sub-Tabs: Comments vs Attachments (shown only when drawer does not manage tabs) */}
      {!hideTabBar && (
        <div className="flex items-center justify-between border-b border-border-subtle pb-2">
          <div className="flex items-center gap-4">
            <button
              type="button"
              onClick={() => setInternalTab('comments')}
              className={`font-label-md text-label-md flex items-center gap-1.5 transition-colors pb-1 ${
                activeTab === 'comments' || activeTab === 'discussion'
                  ? 'text-primary border-b-2 border-primary font-bold'
                  : 'text-text-secondary hover:text-text-primary'
              }`}
            >
              <span className="material-symbols-outlined text-[16px]">forum</span>
              <span>Discussion ({comments.length})</span>
            </button>
            <button
              type="button"
              onClick={() => setInternalTab('attachments')}
              className={`font-label-md text-label-md flex items-center gap-1.5 transition-colors pb-1 ${
                activeTab === 'attachments' || activeTab === 'files'
                  ? 'text-primary border-b-2 border-primary font-bold'
                  : 'text-text-secondary hover:text-text-primary'
              }`}
            >
              <span className="material-symbols-outlined text-[16px]">attach_file</span>
              <span>Files ({attachments.length})</span>
            </button>
          </div>
        </div>
      )}

      {/* COMMENTS / DISCUSSION TAB */}
      {(activeTab === 'comments' || activeTab === 'discussion') && (
        <div className="space-y-4">
          {/* Comment Composer */}
          <div className="bg-surface-card rounded-xl border border-border-subtle shadow-xs overflow-hidden focus-within:ring-2 focus-within:ring-primary/20">
            <textarea
              rows={3}
              value={commentBody}
              onChange={(e) => setCommentBody(e.target.value)}
              placeholder="Add a comment or mention team members with @..."
              className="w-full p-3 font-body-sm text-body-sm text-text-primary placeholder:text-text-muted bg-transparent resize-none focus:outline-none"
            />

            {/* Composer Toolbar */}
            <div className="px-3 py-2 bg-canvas-main border-t border-border-subtle flex items-center justify-between">
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  title="Attach file"
                  onClick={() => fileInputRef.current?.click()}
                  className="w-7 h-7 rounded hover:bg-canvas-lavender text-text-muted hover:text-text-primary flex items-center justify-center transition-colors"
                >
                  <span className="material-symbols-outlined text-[18px]">attach_file</span>
                </button>
                <button
                  type="button"
                  title="Mention"
                  onClick={() => setCommentBody((prev) => `${prev}@`)}
                  className="w-7 h-7 rounded hover:bg-canvas-lavender text-text-muted hover:text-text-primary flex items-center justify-center transition-colors font-mono text-sm"
                >
                  @
                </button>
                <div className="h-4 w-px bg-border-subtle mx-1" />
                <button
                  type="button"
                  title="Format code"
                  onClick={() => setCommentBody((prev) => `${prev}\`code\``)}
                  className="w-7 h-7 rounded hover:bg-canvas-lavender text-text-muted hover:text-text-primary flex items-center justify-center transition-colors"
                >
                  <span className="material-symbols-outlined text-[16px]">code</span>
                </button>
              </div>

              <button
                type="button"
                onClick={handlePostComment}
                disabled={!commentBody.trim() || isPosting}
                className="px-3 py-1.5 rounded-lg bg-btn-primary hover:bg-primary-hover text-on-primary font-label-md text-label-md transition-all flex items-center gap-1 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {isPosting ? (
                  <>
                    <span className="material-symbols-outlined text-[14px] animate-spin">refresh</span>
                    <span>Posting...</span>
                  </>
                ) : (
                  <>
                    <span>Comment</span>
                    <span className="material-symbols-outlined text-[14px]">send</span>
                  </>
                )}
              </button>
            </div>
          </div>

          {/* Comments List */}
          <div className="space-y-3">
            {isLoading && (
              <div className="text-center py-6 text-text-muted font-body-sm flex items-center justify-center gap-2">
                <span className="material-symbols-outlined text-[18px] animate-spin">refresh</span>
                <span>Loading discussion...</span>
              </div>
            )}

            {!isLoading && comments.length === 0 && (
              <div className="text-center py-8 px-4 rounded-xl border border-dashed border-border-subtle bg-canvas-main">
                <span className="material-symbols-outlined text-[28px] text-text-muted">forum</span>
                <p className="font-label-md text-label-md text-text-primary mt-1">No comments yet</p>
                <p className="font-body-sm text-body-sm text-text-secondary">
                  Start the discussion using the composer above.
                </p>
              </div>
            )}

            {!isLoading && comments.map((c) => {
              const authorName = getAuthorName(c);
              const authorRole = getAuthorRole(c);
              const authorAvatar = getAuthorAvatar(c);

              return (
                <div
                  key={c.id}
                  className="p-3.5 rounded-xl bg-surface-card border border-border-subtle space-y-2 shadow-xs"
                >
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <Avatar
                        src={authorAvatar}
                        name={authorName}
                        size="xs"
                        shape="rounded-full"
                      />
                      <span className="font-label-md text-label-md text-text-primary">
                        {authorName}
                      </span>
                      {authorRole && (
                        <span className="px-1.5 py-0.5 rounded bg-canvas-lavender text-text-secondary text-[10px] font-semibold uppercase tracking-wider">
                          {authorRole}
                        </span>
                      )}
                    </div>
                    <span
                      className="font-label-sm text-label-sm text-text-muted"
                      title={c.created_at ? new Date(c.created_at).toLocaleString() : ''}
                    >
                      {formatTimestamp(c.created_at)}
                    </span>
                  </div>
                  <p className="font-body-sm text-body-sm text-text-primary whitespace-pre-wrap pl-8">
                    {c.body}
                  </p>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* ATTACHMENTS / FILES TAB */}
      {(activeTab === 'attachments' || activeTab === 'files') && (
        <div className="space-y-4">
          {/* Dropzone */}
          <div
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
            onClick={() => fileInputRef.current?.click()}
            className={`border-2 border-dashed rounded-xl p-6 text-center cursor-pointer transition-colors ${
              isDragging
                ? 'border-primary bg-canvas-lavender'
                : 'border-border-subtle hover:border-primary/50 bg-canvas-main'
            }`}
          >
            <span className="material-symbols-outlined text-[32px] text-primary">cloud_upload</span>
            <p className="font-label-md text-label-md text-text-primary mt-1">
              Click to browse or drag and drop files here
            </p>
            <p className="font-body-sm text-body-sm text-text-muted mt-0.5">
              Support for documents, images, logs and archives
            </p>
            {isUploading && (
              <div className="mt-2 text-primary font-label-sm text-label-sm flex items-center justify-center gap-1.5">
                <span className="material-symbols-outlined text-[16px] animate-spin">refresh</span>
                <span>Uploading...</span>
              </div>
            )}
          </div>

          <input
            ref={fileInputRef}
            type="file"
            multiple
            onChange={(e) => handleFileSelect(e.target.files)}
            className="hidden"
          />

          {/* Attachment Items List */}
          <div className="space-y-2">
            {!isLoading && attachments.length === 0 && (
              <div className="text-center py-6 text-text-muted font-body-sm">
                No attachments uploaded for this task.
              </div>
            )}

            {attachments.map((att) => (
              <div
                key={att.id}
                className="flex items-center justify-between p-3 rounded-xl bg-surface-card border border-border-subtle"
              >
                <div className="flex items-center gap-3 min-w-0">
                  <div className="w-8 h-8 rounded-lg bg-canvas-lavender text-primary flex items-center justify-center flex-shrink-0">
                    <span className="material-symbols-outlined text-[18px]">description</span>
                  </div>
                  <div className="min-w-0">
                    <p className="font-label-md text-label-md text-text-primary truncate">
                      {att.filename}
                    </p>
                    <p className="font-body-sm text-body-sm text-text-muted">
                      {formatBytes(att.file_size)} • {formatTimestamp(att.created_at)}
                    </p>
                  </div>
                </div>

                <div className="flex items-center gap-1 flex-shrink-0">
                  <a
                    href={att.file_url || `/api/v1/files/${att.id}/download`}
                    target="_blank"
                    rel="noopener noreferrer"
                    download={att.filename || 'attachment'}
                    className="w-8 h-8 rounded-lg hover:bg-canvas-lavender text-text-muted hover:text-text-primary flex items-center justify-center transition-colors"
                    title="Download"
                  >
                    <span className="material-symbols-outlined text-[18px]">download</span>
                  </a>
                  <button
                    type="button"
                    onClick={() => setDeletingAttachment(att)}
                    className="w-8 h-8 rounded-lg hover:bg-error-container/40 text-text-muted hover:text-error flex items-center justify-center transition-colors"
                    title="Delete attachment"
                  >
                    <span className="material-symbols-outlined text-[18px]">delete</span>
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Delete Attachment Confirmation Modal */}
      {deletingAttachment && (
        <Modal
          isOpen={Boolean(deletingAttachment)}
          onClose={() => setDeletingAttachment(null)}
          title="Delete Attachment"
          subtitle={`Are you sure you want to delete "${deletingAttachment.filename}"? This action cannot be undone.`}
          size="sm"
          footer={
            <div className="flex items-center justify-end gap-2 w-full">
              <button
                type="button"
                onClick={() => setDeletingAttachment(null)}
                className="px-4 py-2 rounded-xl border border-border-subtle text-text-secondary hover:bg-canvas-lavender font-label-md"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleDeleteAttachment}
                disabled={isDeleting}
                className="px-4 py-2 rounded-xl bg-error hover:bg-error/90 text-white font-label-md flex items-center gap-1.5 disabled:opacity-50"
              >
                {isDeleting ? 'Deleting...' : 'Delete'}
              </button>
            </div>
          }
        >
          <p className="text-body-sm text-text-secondary">
            File: <strong className="text-text-primary">{deletingAttachment.filename}</strong> ({formatBytes(deletingAttachment.file_size)})
          </p>
        </Modal>
      )}
    </div>
  );
};

export default CommentComposerAndAttachmentPicker;
