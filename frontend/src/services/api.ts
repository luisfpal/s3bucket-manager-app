/**
 * API Service — Multi-tenant Bucket Explorer
 *
 * All requests include X-Tenant-ID header when a tenant is active.
 * baseURL = '/api' — nginx proxies to Django backend.
 */

import axios from 'axios';
import type {
  User, TokenExchangeResponse, Bucket, BucketDetail, TenantInfo, BucketShare,
  BucketAccessList,
  AdminPermission, AdminBucket, AdminUser, AdminTenant, AdminGroupMapping,
  AdminTenantActivation, AdminUOMapping, AdminAvailableTenant, CreateTenantPayload, NexusDetectResponse,
  AdminUserFile, FileNameRule, FileDeviation, TenantDocument, FileFormatsResponse,
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
  /**
   * Exchange the OAuth2 session cookie for JWT access + refresh tokens.
   *
   * Called once from AuthCallback after OIDC flow completes. The server-side
   * session is destroyed after handoff — subsequent requests use JWT, not cookies.
   * @returns JWT tokens, user profile, and all tenant memberships.
   */
  exchangeToken: async (): Promise<TokenExchangeResponse> => {
    const response = await api.get('/auth/token/');
    return response.data;
  },

  /**
   * Fetch the authenticated user's current profile and live tenant memberships.
   *
   * Called on every page mount (Dashboard, BucketDetail, Profile). The response
   * includes each tenant's document visibility — used by Navbar to show/hide the
   * tenant documentation tab without a separate API call.
   * @param opts.signal - Optional AbortSignal to cancel stale requests on navigation.
   */
  getCurrentUser: async (opts: { signal?: AbortSignal } = {}): Promise<User> => {
    const response = await api.get('/auth/user/', { signal: opts.signal });
    return response.data;
  },

  /**
   * Fetch the active tenant's markdown documentation (if visible to users).
   *
   * Returns 404 when no document exists or when the admin has hidden it.
   * The Navbar tab is only shown when this succeeds.
   */
  getTenantDocument: async (): Promise<{ tab_name: string; content: string; updated_at: string }> => {
    const response = await api.get('/auth/tenant-document/');
    return response.data;
  },

  /**
   * Set the active tenant for the current session.
   *
   * Multi-tenant users must call this after login. The returned tenant ID is stored
   * in localStorage and sent as `X-Tenant-ID` on all subsequent bucket requests.
   */
  selectTenant: async (tenantId: number): Promise<{ active_tenant: TenantInfo }> => {
    const response = await api.post('/auth/select-tenant/', { tenant_id: tenantId });
    return response.data;
  },

  /** Clear JWT tokens and active tenant from storage — effectively logs out. */
  logout: () => {
    authTokenStore.clear();
    localStorage.removeItem('active_tenant_id');
    localStorage.removeItem('active_tenant');
  },

  /** Redirect to Authentik OIDC login — initiates the full OAuth2 flow. */
  startLogin: () => {
    authTokenStore.clear();
    localStorage.removeItem('active_tenant_id');
    localStorage.removeItem('active_tenant');
    window.location.href = '/api/oauth/login/authentik/';
  },

  /**
   * Read the active tenant from localStorage (synchronous, no network call).
   *
   * Written by `setActiveTenant` at login or tenant selection. Contains the
   * `document` field used by Navbar to decide whether to show the doc tab.
   */
  getActiveTenant: (): TenantInfo | null => {
    const stored = localStorage.getItem('active_tenant');
    return stored ? JSON.parse(stored) : null;
  },

  /** Persist the active tenant to localStorage for X-Tenant-ID header injection. */
  setActiveTenant: (tenant: TenantInfo) => {
    localStorage.setItem('active_tenant_id', String(tenant.id));
    localStorage.setItem('active_tenant', JSON.stringify(tenant));
  },
};

export const bucketAPI = {
  /**
   * List all buckets the user can access in the active tenant.
   *
   * Requires `X-Tenant-ID` header (injected automatically from localStorage).
   * Returns both proposal (RGWSquared-synced) and local (user-created) buckets
   * with S3 storage stats.
   */
  list: async (): Promise<Bucket[]> => {
    const response = await api.get('/buckets/');
    return response.data;
  },

  /**
   * Create a local research bucket in the active tenant.
   *
   * The backend generates the internal S3 bucket name from the active account.
   * Only `name` (the project ID/display bucket name) is provided by the user.
   */
  create: async (name: string, description?: string): Promise<Bucket> => {
    const response = await api.post('/buckets/', { name, description: description || '' });
    return response.data;
  },

  get: async (id: number, opts: { signal?: AbortSignal } = {}): Promise<BucketDetail> => {
    const response = await api.get(`/buckets/${id}/`, { signal: opts.signal });
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

  downloadArchive: async (bucketId: number, filename: string): Promise<void> => {
    const response = await api.get(`/buckets/${bucketId}/download-archive/`, {
      responseType: 'blob',
    });
    const url = window.URL.createObjectURL(new Blob([response.data]));
    const link = document.createElement('a');
    link.href = url;
    link.setAttribute('download', filename.endsWith('.zip') ? filename : `${filename}.zip`);
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.URL.revokeObjectURL(url);
  },

  getAccessList: async (bucketId: number, opts: { signal?: AbortSignal } = {}): Promise<BucketAccessList> => {
    const response = await api.get(`/buckets/${bucketId}/access-list/`, { signal: opts.signal });
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

  isViewableFile: (fileKey: string): 'image' | 'text' | 'pdf' | 'csv' | 'html' | 'markdown' | 'docx' | null => {
    const ext = fileKey.toLowerCase().split('.').pop() || '';
    if (['png', 'jpg', 'jpeg', 'gif', 'svg', 'bmp', 'webp', 'tiff', 'tif'].includes(ext)) return 'image';
    if (ext === 'csv') return 'csv';
    if (['html', 'htm'].includes(ext)) return 'html';
    if (ext === 'md') return 'markdown';
    if (['txt', 'json', 'xml', 'yaml', 'yml', 'log', 'py', 'js', 'ts', 'sh', 'conf', 'ini', 'cfg', 'toml', 'env', 'css'].includes(ext)) return 'text';
    if (ext === 'pdf') return 'pdf';
    if (ext === 'docx') return 'docx';
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
  startLogin: () => {
    adminTokenStore.clear();
    window.location.href = '/api/oauth/login/authentik/?next=/admin/auth/callback';
  },

  exchangeToken: async () => {
    const response = await axios.get('/api/admin/auth/token/');
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

  getUsers: async (tenantCode?: string): Promise<AdminUser[]> => {
    const url = tenantCode ? `/users/?tenant_code=${encodeURIComponent(tenantCode)}` : '/users/';
    const response = await adminAxios.get(url);
    return response.data;
  },

  getTenantActivation: async (): Promise<AdminTenantActivation[]> => {
    const response = await adminAxios.get('/tenant-activation/');
    return response.data;
  },

  addGroupMapping: async (authentik_group: string, tenant_id: number, role: 'rw' | 'ro' = 'rw'): Promise<AdminGroupMapping> => {
    const response = await adminAxios.post('/group-mappings/', { authentik_group, tenant_id, role });
    return response.data;
  },

  deleteGroupMapping: async (id: number): Promise<void> => {
    await adminAxios.delete(`/group-mappings/${id}/`);
  },

  getAvailableTenants: async (): Promise<AdminAvailableTenant[]> => {
    const response = await adminAxios.get('/available-tenants/');
    return response.data;
  },

  createTenant: async (payload: CreateTenantPayload): Promise<AdminTenant> => {
    const response = await adminAxios.post('/tenants/create/', payload);
    return response.data;
  },

  getUOMappings: async (): Promise<AdminUOMapping[]> => {
    const response = await adminAxios.get('/uo-mappings/');
    return response.data;
  },

  // Operators can force RGWSquared refresh; routine upstream sync is scheduled by RGWSquared.
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

  syncUpdateStructure: async (structure: string, updateFromExt = true) => {
    const response = await adminAxios.post('/sync/update-structure/', {
      structure,
      update_from_ext: updateFromExt,
    }, { timeout: 120000 });
    return response.data;
  },

  getMembershipFiles: async (membershipId: number): Promise<AdminUserFile[]> => {
    const response = await adminAxios.get(`/memberships/${membershipId}/files/`);
    return response.data;
  },

  getFileNameRules: async (tenantCode?: string): Promise<FileNameRule[]> => {
    const url = tenantCode ? `/file-name-rules/?tenant_code=${tenantCode}` : '/file-name-rules/';
    const response = await adminAxios.get(url);
    return response.data;
  },

  addFileNameRule: async (tenant_code: string, substring: string): Promise<FileNameRule> => {
    const response = await adminAxios.post('/file-name-rules/', { tenant_code, substring });
    return response.data;
  },

  deleteFileNameRule: async (id: number): Promise<void> => {
    await adminAxios.delete(`/file-name-rules/${id}/`);
  },

  getFileDeviations: async (tenantCode: string): Promise<{ no_rules: boolean; deviations: FileDeviation[] }> => {
    const response = await adminAxios.get(`/file-deviations/?tenant_code=${tenantCode}`);
    return response.data;
  },

  getTenantDocument: async (tenantCode: string): Promise<TenantDocument> => {
    const response = await adminAxios.get(`/tenant-documents/${tenantCode}/`);
    return response.data;
  },

  saveTenantDocument: async (tenantCode: string, payload: FormData): Promise<TenantDocument> => {
    const response = await adminAxios.post(`/tenant-documents/${tenantCode}/`, payload, {
      headers: { 'Content-Type': undefined as unknown as string },
    });
    return response.data;
  },

  deleteTenantDocument: async (tenantCode: string): Promise<void> => {
    await adminAxios.delete(`/tenant-documents/${tenantCode}/`);
  },

  getFileFormats: async (tenantCode: string): Promise<FileFormatsResponse> => {
    const response = await adminAxios.get(`/file-formats/?tenant_code=${tenantCode}`);
    return response.data;
  },
};

export default api;
