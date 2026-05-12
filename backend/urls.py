"""URL Configuration for multi-tenant S3 Bucket Manager."""

from django.http import HttpResponse
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from storage.views import (
    exchange_token,
    current_user,
    select_tenant,
    health_check,
    BucketViewSet,
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

router = DefaultRouter()
router.register(r"buckets", BucketViewSet, basename="bucket")

urlpatterns = [
    path("favicon.ico", lambda r: HttpResponse(status=204)),
    path("api/health/", health_check, name="health_check"),
    # OAuth callback URLs are provided by social-auth-app-django.
    path("api/oauth/", include("social_django.urls", namespace="social")),
    path("api/auth/token/", exchange_token, name="exchange_token"),
    path("api/auth/user/", current_user, name="current_user"),
    path("api/auth/select-tenant/", select_tenant, name="select_tenant"),
    path("api/auth/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("api/admin/login/", admin_login, name="admin_login"),
    path("api/admin/permissions/", admin_permissions, name="admin_permissions"),
    path("api/admin/buckets/", admin_buckets, name="admin_buckets"),
    path(
        "api/admin/buckets/<int:bucket_id>/",
        admin_bucket_detail,
        name="admin_bucket_detail",
    ),
    path(
        "api/admin/buckets/<int:bucket_id>/files/<path:file_key>/",
        admin_delete_file,
        name="admin_delete_file",
    ),
    path("api/admin/users/", admin_users, name="admin_users"),
    path("api/admin/tenants/", admin_tenants, name="admin_tenants"),
    path(
        "api/admin/group-mappings/", admin_group_mappings, name="admin_group_mappings"
    ),
    path(
        "api/admin/group-mappings/<int:mapping_id>/",
        admin_group_mapping_delete,
        name="admin_group_mapping_delete",
    ),
    path(
        "api/admin/available-tenants/",
        admin_available_tenants,
        name="admin_available_tenants",
    ),
    path("api/admin/uo-mappings/", admin_uo_mappings, name="admin_uo_mappings"),
    path("api/admin/sync/refresh/", admin_sync_refresh, name="admin_sync_refresh"),
    path(
        "api/admin/sync/upload-csv/",
        admin_sync_upload_csv,
        name="admin_sync_upload_csv",
    ),
    path(
        "api/admin/sync/proposals/", admin_sync_proposals, name="admin_sync_proposals"
    ),
    path("api/admin/sync/generate/", admin_sync_generate, name="admin_sync_generate"),
    path("api/admin/sync/apply/", admin_sync_apply, name="admin_sync_apply"),
    path("api/", include(router.urls)),
]
