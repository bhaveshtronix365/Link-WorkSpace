import { apiClient } from './api';

export const discussionsService = {
  listDiscussions: async (params = {}) => {
    const response = await apiClient.get('/discussions', { params });
    return response.data;
  },

  getDiscussion: async (discussionId) => {
    const response = await apiClient.get(`/discussions/${discussionId}`);
    return response.data;
  },

  createDiscussion: async (data, projectId = null) => {
    const params = projectId ? { project_id: projectId } : {};
    const response = await apiClient.post('/discussions', data, { params });
    return response.data;
  },

  addComment: async (discussionId, data) => {
    const response = await apiClient.post(`/discussions/${discussionId}/comments`, data);
    return response.data;
  },

  toggleReaction: async (discussionId, emoji) => {
    const response = await apiClient.post(`/discussions/${discussionId}/reactions`, { emoji });
    return response.data;
  },

  // Attachment management
  uploadAttachment: async (discussionId, file, onProgress) => {
    const formData = new FormData();
    formData.append('file', file);
    const url = (discussionId && discussionId !== 'new' && discussionId !== 'composer')
      ? `/discussions/${discussionId}/attachments`
      : `/discussions/attachments`;
    const response = await apiClient.post(url, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      onUploadProgress: onProgress,
    });
    return response.data;
  },

  listAttachments: async (discussionId) => {
    const response = await apiClient.get(`/discussions/${discussionId}/attachments`);
    return response.data;
  },

  downloadAttachment: async (discussionId, attachmentId, fileName) => {
    const url = discussionId
      ? `/discussions/${discussionId}/attachments/${attachmentId}/download`
      : `/discussions/attachments/${attachmentId}/download`;
    const response = await apiClient.get(url, {
      responseType: 'blob',
    });
    const blob = new Blob([response.data], {
      type: response.headers['content-type'] || 'application/octet-stream',
    });
    const downloadUrl = window.URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = downloadUrl;
    link.download = fileName || 'attachment';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    window.URL.revokeObjectURL(downloadUrl);
  },

  getAttachmentPreviewBlob: async (discussionId, attachmentId) => {
    const url = discussionId
      ? `/discussions/${discussionId}/attachments/${attachmentId}/preview`
      : `/discussions/attachments/${attachmentId}/preview`;
    const response = await apiClient.get(url, {
      responseType: 'blob',
    });
    return URL.createObjectURL(response.data);
  },

  deleteAttachment: async (discussionId, attachmentId) => {
    const url = discussionId
      ? `/discussions/${discussionId}/attachments/${attachmentId}`
      : `/discussions/attachments/${attachmentId}`;
    const response = await apiClient.delete(url);
    return response.data;
  },
};
