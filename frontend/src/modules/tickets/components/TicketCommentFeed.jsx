import React, { useState, useEffect } from 'react';
import { ticketsService } from '../../../services/ticketsService';
import Modal from '../../../components/common/Modal';

const formatBytes = (bytes, decimals = 1) => {
  if (!bytes || bytes === 0) return '0 B';
  const k = 1024;
  const dm = decimals < 0 ? 0 : decimals;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(dm))} ${sizes[i]}`;
};

const isImageAttachment = (att) => {
  if (!att) return false;
  const mime = (att.content_type || '').toLowerCase();
  if (mime.startsWith('image/')) return true;
  const name = (att.original_name || att.name || att.filename || '').toLowerCase();
  return /\.(png|jpe?g|webp|gif)$/i.test(name);
};

// Authenticated image thumbnail component that fetches binary through the secure endpoint
const AuthenticatedThumbnail = ({ ticketId, attachment, onPreviewClick }) => {
  const [src, setSrc] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    let isMounted = true;
    let url = null;

    const loadPreview = async () => {
      try {
        setLoading(true);
        setError(false);
        const blob = await ticketsService.getAttachmentPreviewBlob(ticketId, attachment.id);
        if (isMounted) {
          url = URL.createObjectURL(blob);
          setSrc(url);
        }
      } catch (err) {
        if (isMounted) {
          setError(true);
        }
      } finally {
        if (isMounted) {
          setLoading(false);
        }
      }
    };

    loadPreview();

    return () => {
      isMounted = false;
      if (url) {
        URL.revokeObjectURL(url);
      }
    };
  }, [ticketId, attachment.id]);

  if (loading) {
    return (
      <div className="w-20 h-20 sm:w-24 sm:h-24 bg-canvas-lavender/60 rounded-lg flex items-center justify-center animate-pulse border border-border-subtle">
        <span className="material-symbols-outlined text-text-muted text-base">image</span>
      </div>
    );
  }

  if (error || !src) {
    return (
      <div className="w-20 h-20 sm:w-24 sm:h-24 bg-red-50 text-red-600 rounded-lg flex flex-col items-center justify-center border border-red-200 text-3xs p-1 text-center">
        <span className="material-symbols-outlined text-base mb-0.5">broken_image</span>
        <span>Preview unavailable</span>
      </div>
    );
  }

  const fileName = attachment.original_name || attachment.name || 'image';

  return (
    <button
      type="button"
      onClick={() => onPreviewClick(attachment, src)}
      className="group relative w-20 h-20 sm:w-24 sm:h-24 rounded-lg overflow-hidden border border-border-subtle bg-surface-card hover:ring-2 hover:ring-primary focus:outline-none focus:ring-2 focus:ring-primary transition-all cursor-pointer text-left shrink-0"
      aria-label={`Preview image ${fileName}`}
    >
      <img
        src={src}
        alt={fileName}
        className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-200"
      />
      <div className="absolute inset-0 bg-black/30 opacity-0 group-hover:opacity-100 group-focus:opacity-100 transition-opacity flex items-center justify-center text-white">
        <span className="material-symbols-outlined text-xl">zoom_in</span>
      </div>
    </button>
  );
};

export const TicketCommentFeed = ({ comments = [], ticketId, onAttachmentDeleted }) => {
  const [previewAttachment, setPreviewAttachment] = useState(null);
  const [previewUrl, setPreviewUrl] = useState(null);
  const [downloadingId, setDownloadingId] = useState(null);

  const handlePreviewOpen = (attachment, url) => {
    setPreviewAttachment(attachment);
    setPreviewUrl(url);
  };

  const handlePreviewClose = () => {
    setPreviewAttachment(null);
    setPreviewUrl(null);
  };

  const handleDownload = async (attachment) => {
    const fileName = attachment.original_name || attachment.name || 'attachment';
    try {
      setDownloadingId(attachment.id);
      await ticketsService.downloadAttachment(ticketId, attachment.id, fileName);
    } catch (err) {
      console.error('Download failed:', err);
    } finally {
      setDownloadingId(null);
    }
  };

  if (!comments || comments.length === 0) {
    return (
      <p className="text-center py-6 text-text-muted font-body-sm">
        No comments yet. Post the first operational update below.
      </p>
    );
  }

  return (
    <div className="space-y-space-md" role="feed" aria-label="Ticket comments feed">
      {comments.map((comment) => {
        const attachments = comment.attachments || [];
        const imageAttachments = attachments.filter((att) => isImageAttachment(att) && !att.is_deleted);
        const fileAttachments = attachments.filter((att) => !isImageAttachment(att) && !att.is_deleted);
        const deletedAttachments = attachments.filter((att) => att.is_deleted);

        return (
          <div
            key={comment.id}
            id={`comment-${comment.id}`}
            tabIndex={-1}
            className="flex gap-space-md scroll-mt-24 rounded-xl transition-all outline-none"
          >
            {/* Avatar */}
            <div className="w-10 h-10 rounded-full bg-secondary-container text-on-secondary-container flex items-center justify-center font-label-md text-label-md font-semibold shrink-0 shadow-xs">
              {comment.author?.full_name
                ? comment.author.full_name.slice(0, 2).toUpperCase()
                : 'U'}
            </div>

            {/* Comment Body */}
            <div className="flex-1 space-y-1.5">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="font-label-md text-label-md text-text-primary font-bold">
                    {comment.author?.full_name || 'Team Member'}
                  </span>
                  <span className="text-text-muted text-xs">
                    •{' '}
                    {new Date(comment.created_at).toLocaleString([], {
                      dateStyle: 'short',
                      timeStyle: 'short',
                    })}
                  </span>
                </div>
              </div>

              {/* Content Card */}
              <div className="p-space-md bg-canvas-lavender/40 rounded-xl space-y-2.5 font-body-md text-body-md text-text-secondary">
                {comment.content && <p className="whitespace-pre-wrap">{comment.content}</p>}

                {/* Image Thumbnails Gallery */}
                {imageAttachments.length > 0 && (
                  <div className="pt-1">
                    <span className="block text-3xs font-semibold text-text-muted uppercase tracking-wider mb-1.5">
                      Images ({imageAttachments.length})
                    </span>
                    <div className="flex flex-wrap gap-2">
                      {imageAttachments.map((imgAtt) => (
                        <AuthenticatedThumbnail
                          key={imgAtt.id}
                          ticketId={ticketId}
                          attachment={imgAtt}
                          onPreviewClick={handlePreviewOpen}
                        />
                      ))}
                    </div>
                  </div>
                )}

                {/* Non-image File Chips */}
                {fileAttachments.length > 0 && (
                  <div className="pt-1">
                    <span className="block text-3xs font-semibold text-text-muted uppercase tracking-wider mb-1.5">
                      Files ({fileAttachments.length})
                    </span>
                    <div className="flex flex-wrap gap-2">
                      {fileAttachments.map((fileAtt) => {
                        const fileName = fileAtt.original_name || fileAtt.name || 'document';
                        const isDownloading = downloadingId === fileAtt.id;
                        return (
                          <div
                            key={fileAtt.id}
                            className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg bg-surface-card border border-border-subtle shadow-2xs hover:border-primary/50 transition-all text-xs"
                          >
                            <span className="material-symbols-outlined text-primary text-base shrink-0">
                              description
                            </span>
                            <div className="flex flex-col min-w-0 max-w-[180px] sm:max-w-[240px]">
                              <span className="font-medium text-text-primary truncate" title={fileName}>
                                {fileName}
                              </span>
                              <span className="text-3xs text-text-muted">
                                {formatBytes(fileAtt.size_bytes)}
                              </span>
                            </div>
                            <button
                              type="button"
                              onClick={() => handleDownload(fileAtt)}
                              disabled={isDownloading}
                              className="ml-1 p-1 rounded-md text-primary hover:bg-primary/10 transition-colors focus:outline-none focus:ring-1 focus:ring-primary shrink-0 flex items-center gap-1 font-semibold text-3xs"
                              aria-label={`Download ${fileName}`}
                            >
                              <span className="material-symbols-outlined text-sm">
                                {isDownloading ? 'hourglass_top' : 'download'}
                              </span>
                              <span className="hidden sm:inline">
                                {isDownloading ? 'Downloading...' : 'Download'}
                              </span>
                            </button>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}

                {/* Removed / Deleted Files State */}
                {deletedAttachments.length > 0 && (
                  <div className="pt-1 flex flex-wrap gap-2">
                    {deletedAttachments.map((delAtt) => (
                      <div
                        key={delAtt.id}
                        className="inline-flex items-center gap-1.5 px-3 py-1 rounded-lg bg-surface-base/80 border border-dashed border-border-subtle text-text-muted text-xs"
                      >
                        <span className="material-symbols-outlined text-sm">block</span>
                        <span className="line-through">{delAtt.name || 'Attachment'}</span>
                        <span className="text-3xs italic">(removed)</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>
        );
      })}

      {/* Accessible Image Preview Modal */}
      {previewAttachment && (
        <Modal
          isOpen={Boolean(previewAttachment)}
          onClose={handlePreviewClose}
          title={previewAttachment.original_name || previewAttachment.name || 'Image Preview'}
          subtitle={`${formatBytes(previewAttachment.size_bytes)} • Image Preview`}
          size="lg"
          footer={
            <div className="flex items-center justify-between w-full">
              <span className="text-xs text-text-muted truncate max-w-[250px]">
                {previewAttachment.original_name || previewAttachment.name}
              </span>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => handleDownload(previewAttachment)}
                  className="px-3.5 py-1.5 rounded-lg bg-primary hover:bg-primary-hover text-on-primary text-xs font-semibold shadow-xs flex items-center gap-1.5 transition-colors focus:outline-none focus:ring-2 focus:ring-primary/40 cursor-pointer"
                >
                  <span className="material-symbols-outlined text-sm">download</span>
                  <span>Download</span>
                </button>
                <button
                  type="button"
                  onClick={handlePreviewClose}
                  className="px-3.5 py-1.5 rounded-lg border border-border-subtle hover:bg-surface-base text-text-secondary text-xs font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-primary/40 cursor-pointer"
                >
                  Close
                </button>
              </div>
            </div>
          }
        >
          <div className="flex items-center justify-center p-2 min-h-[300px]">
            {previewUrl ? (
              <img
                src={previewUrl}
                alt={previewAttachment.original_name || previewAttachment.name || 'Preview'}
                className="max-h-[65vh] max-w-full object-contain rounded-lg shadow-sm"
              />
            ) : (
              <div className="flex flex-col items-center justify-center text-text-muted py-12">
                <span className="material-symbols-outlined text-4xl mb-2 animate-spin">progress_activity</span>
                <span>Loading image preview...</span>
              </div>
            )}
          </div>
        </Modal>
      )}
    </div>
  );
};
