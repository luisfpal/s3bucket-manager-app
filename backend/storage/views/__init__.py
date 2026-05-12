"""Views package — split by domain for maintainability."""

from .auth import exchange_token, current_user, select_tenant, health_check
from .buckets import BucketViewSet
from .admin import (
    admin_login,
    admin_permissions,
    admin_buckets,
    admin_bucket_detail,
    admin_delete_file,
    admin_users,
    admin_tenants,
    admin_group_mappings,
    admin_group_mapping_delete,
    admin_available_tenants,
    admin_uo_mappings,
    admin_sync_refresh,
    admin_sync_upload_csv,
    admin_sync_proposals,
    admin_sync_generate,
    admin_sync_apply,
)

__all__ = [
    "exchange_token",
    "current_user",
    "select_tenant",
    "health_check",
    "BucketViewSet",
    "admin_login",
    "admin_permissions",
    "admin_buckets",
    "admin_bucket_detail",
    "admin_delete_file",
    "admin_users",
    "admin_tenants",
    "admin_group_mappings",
    "admin_group_mapping_delete",
    "admin_available_tenants",
    "admin_uo_mappings",
    "admin_sync_refresh",
    "admin_sync_upload_csv",
    "admin_sync_proposals",
    "admin_sync_generate",
    "admin_sync_apply",
]
