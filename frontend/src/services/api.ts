/**
 * API Service — Multi-tenant S3 Bucket Manager
 *
 * All requests include X-Tenant-ID header when a tenant is active.
 * baseURL = '/api' — nginx proxies to Django backend.
 */

import axios from 'axios';
import type {
  User, TokenExchangeResponse, Bucket, BucketDetail, TenantInfo, BucketShare,
  BucketAccessList,
  AdminPermission, AdminBucket, AdminUser, AdminTenant, AdminGroupMapping,
  AdminUOMapping, AdminAvailableTenant, NexusDetectResponse,
} from '../types';

interface FileBlobOptions {
  signal?: AbortSignal;
  onProgress?: (percent: number | null) => void;
}

const api = axios.create({
  baseURL: '/api',
  headers: { 'Content-Type': 'application/json' },
});

const authTokenStore = {
  getAccess: (): string | null => sessionStorage.getItem('access_token'),
  getRefresh: (): string | null => sessionStorage.getItem('refresh_token'),
  setAccess: (token: string) => sessionStorage.setItem('access_token', token),
  setRefresh: (token: string) => sessionStorage.setItem('refresh_token', token),
  clear: () => {
    sessionStorage.removeItem('access_token');
    sessionStorage.removeItem('refresh_token');
  },
};

const adminTokenStore = {
  getAccess: (): string | null => sessionStorage.getItem('admin_access_token'),
  getRefresh: (): string | null => sessionStorage.getItem('admin_refresh_token'),
  setAccess: (token: string) => sessionStorage.setItem('admin_access_token', token),
  setRefresh: (token: string) => sessionStorage.setItem('admin_refresh_token', token),
  clear: () => {
    sessionStorage.removeItem('admin_access_token');
    sessionStorage.removeItem('admin_refresh_token');
    sessionStorage.removeItem('admin_username');
  },
};

export const authStorage = {
  isAuthenticated: () => Boolean(authTokenStore.getAccess()),
  setTokens: (access: string, refresh: string) => {
    authTokenStore.setAccess(access);
    authTokenStore.setRefresh(refresh);
  },
  clearTokens: () => authTokenStore.clear(),
  setAdminTokens: (access: string, refresh: string, username: string) => {
    adminTokenStore.setAccess(access);
    adminTokenStore.setRefresh(refresh);
    sessionStorage.setItem('admin_username', username);
  },
  clearAdminTokens: () => adminTokenStore.clear(),
  getAdminUsername: () => sessionStorage.getItem('admin_username') || 'Admin',
  isAdminAuthenticated: () => Boolean(adminTokenStore.getAccess()),
};

// The tenant header is the backend's isolation boundary for bucket operations.
api.interceptors.request.use((config) => {
  const token = authTokenStore.getAccess();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  const tenantId = localStorage.getItem('active_tenant_id');
  if (tenantId) {
    config.headers['X-Tenant-ID'] = tenantId;
  }
  return config;
});

// Refresh once on 401; a failed refresh clears tenant context and restarts login.
api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config;
    if (error.response?.status === 401 && !originalRequest._retry) {
      originalRequest._retry = true;
      try {
        const refreshToken = authTokenStore.getRefresh();
        if (!refreshToken) throw new Error('No refresh token');
        const response = await axios.post('/api/auth/refresh/', { refresh: refreshToken });
        const newAccessToken = response.data.access;
        authTokenStore.setAccess(newAccessToken);
        originalRequest.headers.Authorization = `Bearer ${newAccessToken}`;
        return api(originalRequest);
      } catch {
        authTokenStore.clear();
        localStorage.removeItem('active_tenant_id');
        localStorage.removeItem('active_tenant');
        window.location.href = '/login';
        return Promise.reject(error);
      }
    }
    return Promise.reject(error);
  }
);

/** Extract error message from axios error response, with fallback. */
export function getApiError(err: unknown, fallback = 'Something went wrong'): string {
  const resp = (err as { response?: { data?: { error?: string } } })?.response;
  return resp?.data?.error || fallback;
}

export const authAPI = {
  exchangeToken: async (): Promise<TokenExchangeResponse> => {
    const response = await api.get('/auth/token/');
    return response.data;
  },

  getCurrentUser: async (): Promise<User> => {
    const response = await api.get('/auth/user/');
    return response.data;
  },

  selectTenant: async (tenantId: number): Promise<{ active_tenant: TenantInfo }> => {
    const response = await api.post('/auth/select-tenant/', { tenant_id: tenantId });
    return response.data;
  },

  logout: () => {
    authTokenStore.clear();
    localStorage.removeItem('active_tenant_id');
    localStorage.removeItem('active_tenant');
  },

  startLogin: () => {
    window.location.href = '/api/oauth/login/authentik/';
  },

  getActiveTenant: (): TenantInfo | null => {
    const stored = localStorage.getItem('active_tenant');
    return stored ? JSON.parse(stored) : null;
  },

  setActiveTenant: (tenant: TenantInfo) => {
    localStorage.setItem('active_tenant_id', String(tenant.id));
    localStorage.setItem('active_tenant', JSON.stringify(tenant));
  },
};

export const bucketAPI = {
  list: async (): Promise<Bucket[]> => {
    const response = await api.get('/buckets/');
    return response.data;
  },

  create: async (name: string, description?: string): Promise<Bucket> => {
    const response = await api.post('/buckets/', { name, description: description || '' });
    return response.data;
  },

  get: async (id: number): Promise<BucketDetail> => {
    const response = await api.get(`/buckets/${id}/`);
    return response.data;
  },

  delete: async (id: number): Promise<void> => {
    await api.delete(`/buckets/${id}/`);
  },

  uploadFile: async (bucketId: number, file: File, key?: string): Promise<{ message: string; key: string }> => {
    const formData = new FormData();
    formData.append('file', file);
    if (key) formData.append('key', key);
    const response = await api.post(`/buckets/${bucketId}/upload/`, formData, {
      headers: { 'Content-Type': undefined as unknown as string },
    });
    return response.data;
  },

  deleteFile: async (bucketId: number, fileKey: string): Promise<void> => {
    await api.delete(`/buckets/${bucketId}/files/${fileKey}/`);
  },

  downloadFile: async (bucketId: number, fileKey: string): Promise<void> => {
    const response = await api.get(`/buckets/${bucketId}/download/${fileKey}/`, {
      responseType: 'blob',
    });
    const url = window.URL.createObjectURL(new Blob([response.data]));
    const link = document.createElement('a');
    link.href = url;
    link.setAttribute('download', fileKey.split('/').pop() || fileKey);
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.URL.revokeObjectURL(url);
  },

  getAccessList: async (bucketId: number): Promise<BucketAccessList> => {
    const response = await api.get(`/buckets/${bucketId}/access-list/`);
    return response.data;
  },

  getShares: async (bucketId: number): Promise<BucketShare[]> => {
    const response = await api.get(`/buckets/${bucketId}/shares/`);
    return response.data;
  },

  addShare: async (bucketId: number, username: string, permission: 'ro' | 'rw'): Promise<BucketShare> => {
    const response = await api.post(`/buckets/${bucketId}/shares/`, { username, permission });
    return response.data;
  },

  removeShare: async (bucketId: number, shareId: number): Promise<void> => {
    await api.delete(`/buckets/${bucketId}/shares/`, { data: { share_id: shareId } });
  },

  leaveBucket: async (bucketId: number): Promise<void> => {
    await api.delete(`/buckets/${bucketId}/leave/`);
  },

  isLikelyNexusFile: (fileKey: string): boolean => {
    const lower = fileKey.toLowerCase();
    return ['.nxs', '.nx5', '.h5', '.hdf5', '.hdf', '.nexus', '.nxspe'].some(ext => lower.endsWith(ext));
  },

  isViewableFile: (fileKey: string): 'image' | 'text' | 'pdf' | 'csv' | 'html' | 'markdown' | null => {
    const ext = fileKey.toLowerCase().split('.').pop() || '';
    if (['png', 'jpg', 'jpeg', 'gif', 'svg', 'bmp', 'webp', 'tiff', 'tif'].includes(ext)) return 'image';
    if (ext === 'csv') return 'csv';
    if (['html', 'htm'].includes(ext)) return 'html';
    if (ext === 'md') return 'markdown';
    if (['txt', 'json', 'xml', 'yaml', 'yml', 'log', 'py', 'js', 'ts', 'sh', 'conf', 'ini', 'cfg', 'toml', 'env', 'css'].includes(ext)) return 'text';
    if (ext === 'pdf') return 'pdf';
    return null;
  },

  encodeFileKey: (fileKey: string): string => encodeURIComponent(fileKey),

  getFileBlob: async (bucketId: number, fileKey: string, options?: FileBlobOptions): Promise<Blob> => {
    const response = await api.get(`/buckets/${bucketId}/download/${fileKey}/`, {
      responseType: 'blob',
      signal: options?.signal,
      onDownloadProgress: (evt) => {
        if (!options?.onProgress) return;
        if (!evt.total || evt.total <= 0) {
          options.onProgress(null);
          return;
        }
        const percent = Math.max(0, Math.min(100, Math.round((evt.loaded / evt.total) * 100)));
        options.onProgress(percent);
      },
    });
    return response.data as Blob;
  },

  nexusDetect: async (bucketId: number, fileKey: string): Promise<NexusDetectResponse> => {
    const encodedKey = encodeURIComponent(fileKey);
    const response = await api.get(`/buckets/${bucketId}/nexus-detect/${encodedKey}/`);
    return response.data;
  },
};

// Admin endpoints use a separate staff-login JWT, not the user OAuth token.
const adminAxios = axios.create({
  baseURL: '/api/admin',
  headers: { 'Content-Type': 'application/json' },
});

adminAxios.interceptors.request.use((config) => {
  const token = adminTokenStore.getAccess();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

adminAxios.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config;
    if (error.response?.status === 401 && !originalRequest._retry) {
      originalRequest._retry = true;
      try {
        const refreshToken = adminTokenStore.getRefresh();
        if (!refreshToken) throw new Error('No admin refresh token');
        const response = await axios.post('/api/auth/refresh/', { refresh: refreshToken });
        const newAccessToken = response.data.access;
        adminTokenStore.setAccess(newAccessToken);
        originalRequest.headers.Authorization = `Bearer ${newAccessToken}`;
        return adminAxios(originalRequest);
      } catch {
        adminTokenStore.clear();
        window.location.href = '/admin/login';
        return Promise.reject(error);
      }
    }
    return Promise.reject(error);
  }
);

export const adminAPI = {
  login: async (username: string, password: string) => {
    const response = await axios.post('/api/admin/login/', { username, password });
    return response.data as { access: string; refresh: string; username: string; display_name: string };
  },

  logout: () => {
    adminTokenStore.clear();
  },

  isAuthenticated: () => !!adminTokenStore.getAccess(),

  getPermissions: async (refresh = false): Promise<AdminPermission[]> => {
    const params = refresh ? '?refresh=true' : '';
    const response = await adminAxios.get(`/permissions/${params}`);
    return response.data;
  },

  getBuckets: async (): Promise<AdminBucket[]> => {
    const response = await adminAxios.get('/buckets/');
    return response.data;
  },

  getBucket: async (id: number): Promise<AdminBucket> => {
    const response = await adminAxios.get(`/buckets/${id}/`);
    return response.data;
  },

  deleteBucket: async (id: number): Promise<void> => {
    await adminAxios.delete(`/buckets/${id}/`);
  },

  deleteFile: async (bucketId: number, fileKey: string): Promise<void> => {
    await adminAxios.delete(`/buckets/${bucketId}/files/${fileKey}/`);
  },

  getUsers: async (): Promise<AdminUser[]> => {
    const response = await adminAxios.get('/users/');
    return response.data;
  },

  getTenants: async (): Promise<AdminTenant[]> => {
    const response = await adminAxios.get('/tenants/');
    return response.data;
  },

  getGroupMappings: async (): Promise<AdminGroupMapping[]> => {
    const response = await adminAxios.get('/group-mappings/');
    return response.data;
  },

  addGroupMapping: async (authentik_group: string, tenant_id: number): Promise<AdminGroupMapping> => {
    const response = await adminAxios.post('/group-mappings/', { authentik_group, tenant_id });
    return response.data;
  },

  deleteGroupMapping: async (id: number): Promise<void> => {
    await adminAxios.delete(`/group-mappings/${id}/`);
  },

  getAvailableTenants: async (): Promise<AdminAvailableTenant[]> => {
    const response = await adminAxios.get('/available-tenants/');
    return response.data;
  },

  getUOMappings: async (): Promise<AdminUOMapping[]> => {
    const response = await adminAxios.get('/uo-mappings/');
    return response.data;
  },

  // Operators run RGWSquared sync as explicit steps so failures stay recoverable.
  syncRefresh: async (structureCode: string) => {
    const response = await adminAxios.post('/sync/refresh/', { structure_code: structureCode });
    return response.data;
  },

  syncUploadCSV: async (file: File) => {
    const formData = new FormData();
    formData.append('file', file);
    const response = await adminAxios.post('/sync/upload-csv/', formData, {
      headers: { 'Content-Type': undefined as unknown as string },
      timeout: 90000,
    });
    return response.data;
  },

  syncProposals: async () => {
    const response = await adminAxios.post('/sync/proposals/', {}, { timeout: 90000 });
    return response.data;
  },

  syncGenerate: async (tenantCode: string) => {
    const response = await adminAxios.post('/sync/generate/', { tenant_code: tenantCode }, { timeout: 90000 });
    return response.data;
  },

  syncApply: async (structure: string) => {
    const response = await adminAxios.post('/sync/apply/', { structure }, { timeout: 90000 });
    return response.data;
  },
};

export default api;
