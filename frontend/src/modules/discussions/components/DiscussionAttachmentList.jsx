import React, { useState, useEffect } from 'react';
import { discussionsService } from '../../../services/discussionsService';
import Modal from '../../../components/common/Modal';

const formatBytes = (bytes, decimals = 1) => {
  if (!bytes || bytes === 0) return '0 B';
  const k = 1024;
  const dm = decimals < 0 ? 0 : decimals;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(dm))} ${sizes[i]}`;
};

const isImage = (att) => {
  if (!att) return false;
  const mime = (att.content_type || '').toLowerCase();
  if (mime.startsWith('image/')) return true;
  const name = (att.original_name || att.name || att.filename || '').toLowerCase();
  return /\.(png|jpe?g|webp|gif)$/i.test(name);
};

const AuthenticatedDiscussionThumbnail = ({ discussionId, attachment, onPreviewClick }) => {
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
        const objectUrl = await discussionsService.getAttachmentPreviewBlob(discussionId, attachment.id);
        if (isMounted) {
          url = objectUrl;
          setSrc(objectUrl);
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
  }, [discussionId, attachment.id]);

  if (loading) {
    return (
      <div className="w-20 h-20 sm:w-24 sm:h-24 bg-canvas-lavender/60 rounded-lg flex items-center justify-center animate-pulse border border-border-subtle shrink-0">
        <span className="material-symbols-outlined text-text-muted text-base">image</span>
      </div>
    );
  }

  if (error || !src) {
    return (
      <div className="w-20 h-20 sm:w-24 sm:h-24 bg-red-50 text-red-600 rounded-lg flex flex-col items-center justify-center border border-red-200 text-3xs p-1 text-center shrink-0">
        <span className="material-symbols-outlined text-base mb-0.5">broken_image</span>
        <span>Preview unavailable</span>
      </div>
    );
  }

  const fileName = attachment.original_name || attachment.name || attachment.filename || 'image';

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

export const DiscussionAttachmentList = ({
  attachments = [],
  discussionId,
  className = '',
}) => {
  const [previewAttachment, setPreviewAttachment] = useState(null);
  const [previewUrl, setPreviewUrl] = useState(null);
  const [downloadingId, setDownloadingId] = useState(null);

  if (!attachments || attachments.length === 0) {
    return null;
  }

  const images = attachments.filter(isImage);
  const files = attachments.filter((att) => !isImage(att));

  const handleDownload = async (attachment) => {
    const fileName = attachment.original_name || attachment.name || attachment.filename || 'attachment';
    try {
      setDownloadingId(attachment.id);
      await discussionsService.downloadAttachment(discussionId, attachment.id, fileName);
    } catch (err) {
      console.error('Download failed:', err);
    } finally {
      setDownloadingId(null);
    }
  };

  return (
    <div className={`flex flex-col gap-2.5 my-2 ${className}`}>
      {/* Image Thumbnails Grid */}
      {images.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {images.map((img) => (
            <AuthenticatedDiscussionThumbnail
              key={img.id}
              discussionId={discussionId}
              attachment={img}
              onPreviewClick={(att, url) => {
                setPreviewAttachment(att);
                setPreviewUrl(url);
              }}
            />
          ))}
        </div>
      )}

      {/* File Chips */}
      {files.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {files.map((file) => {
            const fileName = file.original_name || file.name || file.filename || 'attachment';
            const isPdf = fileName.toLowerCase().endsWith('.pdf');
            const isJson = fileName.toLowerCase().endsWith('.json');
            const isDownloading = downloadingId === file.id;

            return (
              <div
                key={file.id}
                className="flex items-center gap-2.5 px-3 py-1.5 rounded-lg bg-surface-card border border-border-subtle shadow-2xs hover:border-border-strong transition-all max-w-full"
              >
                <span className="material-symbols-outlined text-[18px] text-text-muted shrink-0">
                  {isPdf ? 'picture_as_pdf' : isJson ? 'data_object' : 'description'}
                </span>
                <div className="flex flex-col min-w-0 pr-1">
                  <span className="font-medium text-xs text-text-primary truncate max-w-[200px] sm:max-w-[280px]" title={fileName}>
                    {fileName}
                  </span>
                  <span className="text-3xs text-text-muted">
                    {formatBytes(file.size_bytes)}
                  </span>
                </div>
                <button
                  type="button"
                  onClick={() => handleDownload(file)}
                  disabled={isDownloading}
                  className="px-2 py-1 rounded-md bg-canvas-lavender hover:bg-surface-container text-primary text-xs font-semibold flex items-center gap-1 transition-colors shrink-0 disabled:opacity-50 cursor-pointer"
                  aria-label={`Download ${fileName}`}
                >
                  <span className="material-symbols-outlined text-[14px]">
                    {isDownloading ? 'progress_activity' : 'download'}
                  </span>
                  <span>{isDownloading ? '...' : 'Download'}</span>
                </button>
              </div>
            );
          })}
        </div>
      )}

      {/* Image Preview Modal */}
      {previewAttachment && (
        <Modal
          isOpen={Boolean(previewAttachment)}
          onClose={() => {
            setPreviewAttachment(null);
            setPreviewUrl(null);
          }}
          title={previewAttachment.original_name || previewAttachment.name || 'Image Preview'}
          subtitle={`${formatBytes(previewAttachment.size_bytes)} • Image Attachment`}
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
                  className="px-3.5 py-1.5 rounded-lg bg-btn-primary hover:bg-primary-hover text-on-primary text-xs font-semibold shadow-xs flex items-center gap-1.5 transition-colors cursor-pointer"
                >
                  <span className="material-symbols-outlined text-sm">download</span>
                  <span>Download</span>
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setPreviewAttachment(null);
                    setPreviewUrl(null);
                  }}
                  className="px-3.5 py-1.5 rounded-lg border border-border-subtle hover:bg-canvas-lavender text-text-secondary text-xs font-semibold transition-colors cursor-pointer"
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
                <span>Loading preview...</span>
              </div>
            )}
          </div>
        </Modal>
      )}
    </div>
  );
};

export default DiscussionAttachmentList;
