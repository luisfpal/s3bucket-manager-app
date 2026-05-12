// Federation fields mirror the backend user payload.
export interface User {
  id: number;
  username: string;
  display_name?: string;
  email: string;
  first_name: string;
  last_name: string;
  external_id: string;
  idp_source: string;
  institution: string;
  department: string;
  affiliation_status: string;
  orcid: string;
  profile_picture_url: string;
  is_approved: boolean;
  is_staff: boolean;
  date_joined: string;
  tenants?: TenantInfo[];
  is_admin?: boolean;
}

export interface TenantInfo {
  id: number;
  code: string;
  name: string;
  role: 'ro' | 'rw' | 'admin';
  uo_code?: string;
  uo_name?: string;
}

export interface NexusDetectResponse {
  is_nexus: boolean;
  size: number;
  filename: string;
}

export interface AuthTokens {
  access: string;
  refresh: string;
}

export interface TokenExchangeResponse {
  user: User;
  tokens: AuthTokens;
  tenants: TenantInfo[];
  active_tenant: TenantInfo | null;
  tenant_selection_required: boolean;
}

export interface Bucket {
  id: number;
  name: string;
  display_name: string;
  description: string;
  bucket_type: 'proposal' | 'local';
  is_deletable: boolean;
  owner: number | null;
  owner_name: string | null;
  permission: 'ro' | 'rw' | 'owner' | null;
  shared_with_count: number;
  size_bytes: number;
  num_objects: number;
  created_at: string;
  updated_at: string;
}

export interface BucketFile {
  key: string;
  size: number;
  last_modified: string;
  uploaded_by?: string | null;
}

export interface BucketDetail extends Bucket {
  files: BucketFile[];
}

export interface BucketShare {
  id: number;
  user_id: number;
  username: string;
  display_name?: string;
  email: string;
  permission: 'ro' | 'rw';
  granted_at: string;
}

export interface BucketAccessEntry {
  user_id: number;
  display_name: string;
  email?: string;
  permission: 'ro' | 'rw' | 'owner';
  source: 'local' | 'rgwsquared';
}

export interface BucketAccessList {
  owner_label: string;
  is_owner: boolean;
  access: BucketAccessEntry[];
}

export interface AdminPermission {
  id: number;
  username: string;
  ceph_username: string;
  email: string;
  bucket_name: string;
  bucket_ceph_name: string;
  tenant_code: string | null;
  permission: string;
  source: string;
  granted_at: string;
}

export interface AdminBucket {
  id: number;
  name: string;
  display_name: string;
  tenant_code: string | null;
  bucket_type: 'proposal' | 'local';
  owner_name: string | null;
  is_deletable: boolean;
  shares_count: number;
  size_bytes: number;
  num_objects: number;
  created_at: string;
}

export interface AdminUser {
  id: number;
  user_id: number;
  ceph_username: string;
  display_name: string;
  email: string;
  tenant_code: string;
  role: string;
  uo_code: string;
  last_login: string | null;
}

export interface AdminTenant {
  id: number;
  code: string;
  name: string;
  member_count: number;
  bucket_count: number;
  storage_bytes: number;
  mgmt_keys_updated_at: string | null;
}

export interface AdminGroupMapping {
  id: number;
  authentik_group: string;
  tenant_code: string;
  tenant_id: number;
}

export interface AdminUOMapping {
  id: number;
  uo_code: string;
  institution_name: string;
  tenant_code: string;
}

export interface AdminAvailableTenant {
  structure: string;
  has_tenant: boolean;
  tenant_id: number | null;
}
