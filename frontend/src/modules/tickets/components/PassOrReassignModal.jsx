import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { Modal } from '../../../components/common/Modal';
import { membersService } from '../../../services/membersService';
import { projectsService } from '../../../services/projectsService';
import { ticketsService } from '../../../services/ticketsService';
import { authService } from '../../../services/api';
import { PRESENCE_CONFIG, PRESENCE_STATUS } from '../../../constants/presence';

const formatBytes = (bytes, decimals = 1) => {
  if (!bytes || bytes === 0) return '0 B';
  const k = 1024;
  const dm = decimals < 0 ? 0 : decimals;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(dm))} ${sizes[i]}`;
};

export const PassOrReassignModal = ({
  isOpen,
  onClose,
  ticket,
  mode = 'pass', // 'pass' | 'reassign'
  onSuccess,
  onError,
}) => {
  const [members, setMembers] = useState([]);
  const [loadingMembers, setLoadingMembers] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedUserId, setSelectedUserId] = useState('');
  const [message, setMessage] = useState('');
  const [pendingFiles, setPendingFiles] = useState([]);
  const [isDragging, setIsDragging] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [localError, setLocalError] = useState(null);

  const fileInputRef = useRef(null);
  const dropZoneRef = useRef(null);

  const currentUser = authService.getCurrentUserSync();
  const currentUserId = currentUser?.id || currentUser?.sub;

  const prevIsOpenRef = useRef(false);
  const prevTicketIdRef = useRef(ticket?.id);
  const latestTicketRef = useRef(ticket);

  useEffect(() => {
    latestTicketRef.current = ticket;
  }, [ticket]);

  const fetchMembers = useCallback(async () => {
    setLoadingMembers(true);
    setLocalError(null);
    try {
      let data = await membersService.listMembers();
      let list = Array.isArray(data) ? data : (data?.users || data?.items || data?.members || data?.data || []);
      if (!list || list.length === 0) {
        try {
          const fallbackData = await projectsService.getUsers();
          const fallbackList = Array.isArray(fallbackData)
            ? fallbackData
            : (fallbackData?.users || fallbackData?.items || fallbackData?.members || fallbackData?.data || []);
          if (fallbackList && fallbackList.length > 0) {
            list = fallbackList;
          }
        } catch {
          // keep original list
        }
      }
      setMembers(list);
    } catch (err) {
      console.error('Failed to load members via membersService, trying fallback:', err);
      try {
        const fallbackData = await projectsService.getUsers();
        const fallbackList = Array.isArray(fallbackData)
          ? fallbackData
          : (fallbackData?.users || fallbackData?.items || fallbackData?.members || fallbackData?.data || []);
        if (fallbackList && fallbackList.length > 0) {
          setMembers(fallbackList);
          return;
        }
      } catch (fbErr) {
        console.error('Fallback member fetch also failed:', fbErr);
      }
      setLocalError('Failed to load active organization members.');
    } finally {
      setLoadingMembers(false);
    }
  }, []);

  // Reset modal state only when opening from closed state or when ticket ID changes
  useEffect(() => {
    const isNowOpen = Boolean(isOpen);
    const wasOpen = prevIsOpenRef.current;
    const ticketIdChanged = ticket?.id && ticket?.id !== prevTicketIdRef.current;

    if (isNowOpen && (!wasOpen || ticketIdChanged)) {
      setSearchQuery('');
      setSelectedUserId('');
      setMessage('');
      setPendingFiles([]);
      setLocalError(null);
      setSubmitting(false);

      fetchMembers();
    }

    prevIsOpenRef.current = isNowOpen;
    prevTicketIdRef.current = ticket?.id;
  }, [isOpen, ticket?.id, fetchMembers]);

  // Filter members
  const filteredMembers = useMemo(() => {
    return members.filter((m) => {
      // Must be active and not suspended
      if (m.is_active === false || m.status === 'suspended' || m.account_state === 'inactive') return false;
      // If pass mode: cannot pass to self
      const memberId = m.id || m.user_id;
      if (mode === 'pass' && currentUserId && memberId === currentUserId) return false;
      // Search query filter
      if (searchQuery.trim()) {
        const q = searchQuery.toLowerCase();
        const name = (m.full_name || '').toLowerCase();
        const email = (m.email || '').toLowerCase();
        const role = (m.role || '').toLowerCase();
        return name.includes(q) || email.includes(q) || role.includes(q);
      }
      return true;
    });
  }, [members, mode, currentUserId, searchQuery]);

  // File upload logic
  const uploadSingleFile = useCallback(async (fileItem) => {
    if (!ticket) return;
    setPendingFiles((prev) =>
      prev.map((f) =>
        f.id === fileItem.id ? { ...f, status: 'uploading', progress: 0, error: null } : f
      )
    );

    try {
      const res = await ticketsService.addAttachment(
        ticket.id,
        fileItem.file,
        (progressEvent) => {
          if (progressEvent.total) {
            const pct = Math.round((progressEvent.loaded * 100) / progressEvent.total);
            setPendingFiles((prev) =>
              prev.map((f) => (f.id === fileItem.id ? { ...f, progress: pct } : f))
            );
          }
        }
      );

      setPendingFiles((prev) =>
        prev.map((f) =>
          f.id === fileItem.id ? { ...f, status: 'success', progress: 100, attachmentId: res.id } : f
        )
      );
    } catch (err) {
      const msg =
        err.response?.data?.error?.message ||
        err.response?.data?.detail?.message ||
        err.response?.data?.detail ||
        err.message ||
        'Failed to upload file.';
      setPendingFiles((prev) =>
        prev.map((f) => (f.id === fileItem.id ? { ...f, status: 'error', progress: 0, error: msg } : f))
      );
    }
  }, [ticket?.id]);

  const handleFilesAdded = useCallback((fileList) => {
    if (!fileList || fileList.length === 0) return;
    setLocalError(null);

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
    newItems.forEach((item) => uploadSingleFile(item));
  }, [uploadSingleFile]);

  const handleRetryUpload = (fileId) => {
    const item = pendingFiles.find((f) => f.id === fileId);
    if (item && item.file) {
      uploadSingleFile(item);
    }
  };

  const handleRemoveFile = (fileId) => {
    setPendingFiles((prev) => prev.filter((f) => f.id !== fileId));
  };

  const handlePaste = (e) => {
    const clipboardData = e.clipboardData;
    if (!clipboardData) return;
    if (clipboardData.files && clipboardData.files.length > 0) {
      handleFilesAdded(clipboardData.files);
      return;
    }
    const items = clipboardData.items;
    if (items && items.length > 0) {
      const pastedFiles = [];
      for (let i = 0; i < items.length; i++) {
        const item = items[i];
        if (item.kind === 'file' || item.type.startsWith('image/')) {
          const file = item.getAsFile();
          if (file) {
            const ext = file.type.split('/')[1] || 'png';
            const renamed = new File([file], `handoff_attachment_${Date.now()}.${ext}`, { type: file.type });
            pastedFiles.push(renamed);
          }
        }
      }
      if (pastedFiles.length > 0) {
        handleFilesAdded(pastedFiles);
      }
    }
  };

  // Validation
  const trimmedMessage = message.trim();
  const isMessageValid = trimmedMessage.length >= 10 && trimmedMessage.length <= 1000;
  const isAnyUploading = pendingFiles.some((f) => f.status === 'uploading');
  const isFormValid = Boolean(selectedUserId) && isMessageValid && !isAnyUploading && !submitting;

  const handleSubmit = async (e) => {
    if (e) e.preventDefault();
    if (!isFormValid) return;

    setSubmitting(true);
    setLocalError(null);

    const successfulAttachmentIds = pendingFiles
      .filter((f) => f.status === 'success' && f.attachmentId)
      .map((f) => f.attachmentId);

    try {
      const currentVersion = latestTicketRef.current?.version ?? ticket?.version;
      let updatedTicket;
      if (mode === 'pass') {
        updatedTicket = await ticketsService.passTicket(ticket.id, {
          to_user_id: selectedUserId,
          message: trimmedMessage,
          attachment_ids: successfulAttachmentIds.length > 0 ? successfulAttachmentIds : undefined,
          version: currentVersion,
        });
      } else {
        // mode === 'reassign'
        updatedTicket = await ticketsService.reassignTicketByOwner(ticket.id, {
          assignee_id: selectedUserId,
          message: trimmedMessage,
          attachment_ids: successfulAttachmentIds.length > 0 ? successfulAttachmentIds : undefined,
          version: currentVersion,
        });
      }

      onSuccess?.(updatedTicket);
      onClose();
    } catch (err) {
      console.error('Failed to handoff/reassign ticket:', err);
      const errMsg =
        err.response?.data?.error?.message ||
        err.response?.data?.detail?.message ||
        err.response?.data?.detail ||
        err.message ||
        'Action failed. Please refresh the ticket and try again.';
      setLocalError(errMsg);
      onError?.(errMsg);
    } finally {
      setSubmitting(false);
    }
  };

  const isReassign = mode === 'reassign';
  const modalTitle = isReassign ? 'Reassign Ticket (Owner)' : 'Pass to Colleague';
  const modalSubtitle = isReassign
    ? 'Transfer ticket ownership directly to another active member. The current holder will be notified.'
    : 'Pass operational ownership and context to an active team member.';

  const selectedMember = members.find((m) => m.id === selectedUserId);

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={modalTitle}
      subtitle={modalSubtitle}
      size="lg"
      hasUnsavedChanges={Boolean(message.trim() || selectedUserId || pendingFiles.length > 0)}
      footer={
        <div className="flex items-center justify-between w-full">
          <div className="text-xs text-text-muted">
            {selectedMember ? (
              <span>Selected: <strong className="text-text-primary">{selectedMember.full_name || selectedMember.email}</strong></span>
            ) : (
              <span>Select an active team member</span>
            )}
          </div>
          <div className="flex items-center gap-space-sm">
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
              disabled={!isFormValid}
              className="px-5 py-2 rounded-lg bg-primary hover:bg-primary/90 text-on-primary font-label-md text-label-md font-semibold flex items-center gap-2 shadow-sm transition-all disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {submitting ? (
                <>
                  <span className="material-symbols-outlined text-sm animate-spin">sync</span>
                  <span>Processing...</span>
                </>
              ) : (
                <>
                  <span className="material-symbols-outlined text-sm">
                    {isReassign ? 'assignment_ind' : 'forward'}
                  </span>
                  <span>{isReassign ? 'Confirm Reassignment' : 'Pass Ticket'}</span>
                </>
              )}
            </button>
          </div>
        </div>
      }
    >
      <div className="space-y-space-md text-text-primary">
        {/* Error notification */}
        {localError && (
          <div
            role="alert"
            aria-live="polite"
            className="p-3 rounded-lg bg-error-container text-on-error-container font-body-sm text-body-sm flex items-start justify-between gap-2"
          >
            <div className="flex items-start gap-2">
              <span className="material-symbols-outlined text-base shrink-0 mt-0.5">error</span>
              <p className="font-semibold">{localError}</p>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              <button
                type="button"
                onClick={fetchMembers}
                className="px-2 py-0.5 rounded bg-error/20 hover:bg-error/30 text-xs font-semibold underline cursor-pointer"
              >
                Retry
              </button>
              <button
                type="button"
                onClick={() => setLocalError(null)}
                className="text-text-muted hover:text-text-primary text-xs"
                aria-label="Dismiss error"
              >
                <span className="material-symbols-outlined text-sm">close</span>
              </button>
            </div>
          </div>
        )}

        {/* Reassign notification banner */}
        {isReassign && (
          <div className="p-3 rounded-lg bg-status-wip/10 border border-status-wip/30 flex items-start gap-2.5">
            <span className="material-symbols-outlined text-status-wip text-lg mt-0.5 shrink-0">info</span>
            <div className="text-body-sm text-text-secondary">
              <strong className="text-text-primary">Owner Reassignment Notice:</strong> Current holder{' '}
              <strong className="text-text-primary">
                {ticket?.assignee?.full_name || ticket?.assignee?.email || 'unassigned'}
              </strong>{' '}
              and the newly assigned member will both receive immediate notifications with your message.
            </div>
          </div>
        )}

        {/* 1. Member Selector with Search & Presence */}
        <div className="space-y-1.5">
          <label className="font-label-md text-label-md text-text-primary flex items-center justify-between">
            <span>
              Target Colleague <span className="text-severity-blocker">*</span>
            </span>
            <span className="font-label-sm text-label-sm text-text-muted">
              {filteredMembers.length} active member{filteredMembers.length === 1 ? '' : 's'}
            </span>
          </label>

          {/* Search box */}
          <div className="relative">
            <span className="material-symbols-outlined absolute left-3 top-2.5 text-text-muted text-lg pointer-events-none">
              search
            </span>
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search active colleagues by name, email, or role..."
              className="w-full pl-9 pr-3 py-2 bg-canvas-main rounded-lg border border-border-subtle font-body-sm text-body-sm text-text-primary placeholder:text-text-muted outline-none focus:border-primary transition-colors"
            />
            {searchQuery && (
              <button
                type="button"
                onClick={() => setSearchQuery('')}
                className="absolute right-2.5 top-2.5 text-text-muted hover:text-text-primary text-xs"
                aria-label="Clear search"
              >
                <span className="material-symbols-outlined text-sm">close</span>
              </button>
            )}
          </div>

          {/* Member List */}
          <div
            role="radiogroup"
            aria-label="Select target colleague"
            className="mt-2 max-h-48 overflow-y-auto space-y-1 rounded-lg border border-border-subtle p-1 bg-surface-container-lowest focus:outline-none"
          >
            {loadingMembers ? (
              <div className="flex items-center justify-center py-6 text-text-muted text-body-sm">
                <span className="material-symbols-outlined text-lg animate-spin mr-2">sync</span>
                <span>Loading active team members...</span>
              </div>
            ) : filteredMembers.length === 0 ? (
              <div className="text-center py-6 text-text-muted text-body-sm">
                No active team members found matching "{searchQuery}".
              </div>
            ) : (
              filteredMembers.map((member) => {
                const isSelected = selectedUserId === member.id;
                const presenceStatus = (member.presence || 'offline').toLowerCase();
                const presenceConfig = PRESENCE_CONFIG[presenceStatus] || PRESENCE_CONFIG[PRESENCE_STATUS.OFFLINE];
                const dotColor = presenceConfig?.dotClass || 'bg-gray-400';

                return (
                  <button
                    key={member.id}
                    type="button"
                    role="radio"
                    aria-checked={isSelected}
                    onClick={() => setSelectedUserId(member.id)}
                    className={`w-full flex items-center justify-between p-2 rounded-lg text-left transition-all ${
                      isSelected
                        ? 'bg-primary-container text-on-primary-container ring-1 ring-primary'
                        : 'hover:bg-canvas-lavender text-text-primary'
                    }`}
                  >
                    <div className="flex items-center gap-2.5 min-w-0">
                      {/* Avatar with presence dot */}
                      <div className="relative shrink-0">
                        <div className="w-8 h-8 rounded-full bg-surface-container-high flex items-center justify-center font-label-sm text-label-sm font-bold uppercase text-text-primary">
                          {member.avatar_url ? (
                            <img
                              src={member.avatar_url}
                              alt={member.full_name || member.email}
                              className="w-full h-full rounded-full object-cover"
                            />
                          ) : (
                            (member.full_name || member.email || 'U').slice(0, 2).toUpperCase()
                          )}
                        </div>
                        <span
                          title={`Presence: ${presenceConfig?.label || presenceStatus}`}
                          className={`absolute -bottom-0.5 -right-0.5 w-2.5 h-2.5 rounded-full ${dotColor} ring-2 ring-surface-card`}
                        />
                      </div>

                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="font-label-md text-label-md font-semibold truncate">
                            {member.full_name || member.email}
                          </span>
                          <span className="px-1.5 py-0.2 rounded text-[11px] font-medium bg-canvas-lavender text-text-secondary capitalize">
                            {member.role || 'member'}
                          </span>
                        </div>
                        <p className="font-body-xs text-xs text-text-muted truncate">{member.email}</p>
                      </div>
                    </div>

                    <div className="shrink-0 pl-2">
                      {isSelected ? (
                        <span className="w-5 h-5 rounded-full bg-primary text-on-primary flex items-center justify-center">
                          <span className="material-symbols-outlined text-xs">check</span>
                        </span>
                      ) : (
                        <span className="w-4 h-4 rounded-full border border-border-subtle inline-block" />
                      )}
                    </div>
                  </button>
                );
              })
            )}
          </div>
        </div>

        {/* 2. Required Message Field with Counter */}
        <div className="space-y-1.5">
          <div className="flex items-center justify-between font-label-md text-label-md">
            <label htmlFor="handoff-message" className="text-text-primary">
              {isReassign ? 'Reassignment Context & Instructions' : 'Handoff Message'}{' '}
              <span className="text-severity-blocker">*</span>
            </label>
            <span
              className={`font-mono text-xs ${
                trimmedMessage.length === 0
                  ? 'text-text-muted'
                  : trimmedMessage.length < 10
                  ? 'text-severity-blocker font-semibold'
                  : trimmedMessage.length > 1000
                  ? 'text-severity-blocker font-bold'
                  : 'text-status-resolved font-medium'
              }`}
            >
              {trimmedMessage.length} / 1000 chars {trimmedMessage.length < 10 ? '(min 10)' : ''}
            </span>
          </div>

          <textarea
            id="handoff-message"
            rows="4"
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            onPaste={handlePaste}
            placeholder={
              isReassign
                ? 'Provide detailed operational reason and directives for reassigning this ticket (min 10 characters)...'
                : 'Provide handover notes, findings, relevant logs, and next steps for your colleague (min 10 characters)...'
            }
            className={`w-full p-3 bg-canvas-main rounded-lg border font-body-sm text-body-sm text-text-primary placeholder:text-text-muted outline-none transition-colors resize-y ${
              trimmedMessage.length > 0 && trimmedMessage.length < 10
                ? 'border-severity-blocker focus:border-severity-blocker'
                : 'border-border-subtle focus:border-primary'
            }`}
          />
        </div>

        {/* 3. Optional Attachments Section */}
        <div
          ref={dropZoneRef}
          onDragOver={(e) => { e.preventDefault(); setIsDragging(true); }}
          onDragEnter={(e) => { e.preventDefault(); setIsDragging(true); }}
          onDragLeave={(e) => {
            if (dropZoneRef.current && !dropZoneRef.current.contains(e.relatedTarget)) {
              setIsDragging(false);
            }
          }}
          onDrop={(e) => {
            e.preventDefault();
            setIsDragging(false);
            if (e.dataTransfer?.files) handleFilesAdded(e.dataTransfer.files);
          }}
          className={`space-y-2 p-3 rounded-lg border border-dashed transition-colors ${
            isDragging
              ? 'border-primary bg-primary-container/10'
              : 'border-border-subtle bg-surface-container-lowest'
          }`}
        >
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="material-symbols-outlined text-text-muted text-base">attach_file</span>
              <span className="font-label-md text-label-md text-text-primary">
                Optional Attachments
              </span>
              <span className="text-xs text-text-muted">(Images, PDFs, Logs, Traces)</span>
            </div>
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              className="inline-flex items-center gap-1 px-2.5 py-1 rounded bg-canvas-lavender hover:bg-surface-container text-text-primary font-label-sm text-xs font-semibold transition-colors"
            >
              <span className="material-symbols-outlined text-xs">add</span>
              <span>Upload File</span>
            </button>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              className="hidden"
              onChange={(e) => {
                if (e.target.files && e.target.files.length > 0) {
                  handleFilesAdded(e.target.files);
                }
                e.target.value = '';
              }}
            />
          </div>

          {/* Pending files list */}
          {pendingFiles.length > 0 && (
            <div className="space-y-1.5 pt-1">
              {pendingFiles.map((item) => (
                <div
                  key={item.id}
                  className="flex items-center justify-between gap-2 p-2 rounded bg-surface-card border border-border-subtle text-xs"
                >
                  <div className="flex items-center gap-2 min-w-0 flex-1">
                    <span className="material-symbols-outlined text-base text-primary shrink-0">
                      description
                    </span>
                    <span className="truncate font-medium text-text-primary">{item.name}</span>
                    <span className="text-text-muted font-mono shrink-0">({formatBytes(item.size)})</span>

                    {/* Progress bar */}
                    {item.status === 'uploading' && (
                      <div className="flex items-center gap-1.5 flex-1 max-w-[120px]">
                        <div className="w-full bg-canvas-lavender h-1.5 rounded-full overflow-hidden">
                          <div
                            className="bg-primary h-full transition-all duration-150"
                            style={{ width: `${item.progress}%` }}
                          />
                        </div>
                        <span className="font-mono text-[10px] text-text-muted">{item.progress}%</span>
                      </div>
                    )}

                    {item.status === 'error' && (
                      <span className="text-severity-blocker font-medium truncate">
                        {item.error || 'Upload failed'}
                      </span>
                    )}

                    {item.status === 'success' && (
                      <span className="text-status-resolved flex items-center gap-0.5 shrink-0">
                        <span className="material-symbols-outlined text-xs">check_circle</span>
                        <span>Ready</span>
                      </span>
                    )}
                  </div>

                  <div className="flex items-center gap-1 shrink-0">
                    {item.status === 'error' && (
                      <button
                        type="button"
                        onClick={() => handleRetryUpload(item.id)}
                        className="p-1 text-primary hover:text-primary-hover"
                        title="Retry upload"
                      >
                        <span className="material-symbols-outlined text-xs">refresh</span>
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => handleRemoveFile(item.id)}
                      className="p-1 text-text-muted hover:text-severity-blocker"
                      title="Remove file"
                    >
                      <span className="material-symbols-outlined text-xs">close</span>
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </Modal>
  );
};

export default PassOrReassignModal;
