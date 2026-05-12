"""Bucket CRUD + file operations — tenant-scoped, permission-aware."""

import logging
import re

from django.http import HttpResponse
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from botocore.exceptions import ClientError

from storage.models import (
    Bucket,
    BucketPermission,
    TenantMembership,
    FileUploadRecord,
)
from storage.serializers import (
    BucketSerializer,
    BucketCreateSerializer,
    FileSerializer,
    FileUploadSerializer,
)
from storage.services import permissions as perms
from storage.services.s3_ops import (
    create_bucket as s3_create_bucket,
    delete_bucket as s3_delete_bucket,
    list_objects,
    upload_object,
    delete_object,
    download_object,
    get_mgmt_s3_client,
    get_all_bucket_stats,
)

logger = logging.getLogger(__name__)

PROJECT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,48}[a-z0-9]$")


def _get_membership(request):
    """Extract active tenant membership from request header or query param."""
    tenant_id = request.headers.get("X-Tenant-ID") or request.query_params.get(
        "tenant_id"
    )
    if not tenant_id:
        return None
    try:
        return TenantMembership.objects.select_related("tenant").get(
            user=request.user,
            tenant_id=tenant_id,
            is_active=True,
        )
    except TenantMembership.DoesNotExist:
        return None


class BucketViewSet(viewsets.ViewSet):
    """Tenant-scoped bucket operations.

    All requests must include X-Tenant-ID header or tenant_id query param.

    list:    GET  /api/buckets/?tenant_id=X  — user's accessible buckets in tenant
    create:  POST /api/buckets/              — create local research bucket
    retrieve:GET  /api/buckets/{id}/         — bucket detail + files
    destroy: DELETE /api/buckets/{id}/       — delete bucket (owner only, local only)
    upload:  POST /api/buckets/{id}/upload/  — upload file
    delete_file: DELETE /api/buckets/{id}/files/{key}/ — delete file
    download: GET /api/buckets/{id}/download/{key}/    — download file
    shares:  GET/POST/DELETE /api/buckets/{id}/shares/ — manage sharing
    """

    permission_classes = [IsAuthenticated]

    def list(self, request):
        """List buckets the user can access in the active tenant."""
        membership = _get_membership(request)
        if not membership:
            return Response(
                {"error": "X-Tenant-ID header or tenant_id param required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        tenant = membership.tenant

        permitted_bucket_ids = BucketPermission.objects.filter(
            user=request.user,
            bucket__tenant=tenant,
        ).values_list("bucket_id", flat=True)

        buckets = Bucket.objects.filter(id__in=permitted_bucket_ids).select_related(
            "tenant", "owner"
        )

        # One RGW stats call avoids per-bucket admin API traffic.
        try:
            bucket_stats = get_all_bucket_stats()
        except Exception:
            bucket_stats = {}

        serializer = BucketSerializer(
            buckets,
            many=True,
            context={"user": request.user, "bucket_stats": bucket_stats},
        )
        return Response(serializer.data)

    def create(self, request):
        """Create a local research bucket.

        Naming convention:
        - Internal name (S3): {ceph_username}_{uo}_{project_id} for NFFADI,
          {ceph_username}_{project_id} for others
        - Display name (UI): just project_id
        """
        membership = _get_membership(request)
        if not membership:
            return Response(
                {"error": "X-Tenant-ID header or tenant_id param required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not perms.can_create_bucket(membership):
            return Response(
                {"error": "Read-only users cannot create buckets"},
                status=status.HTTP_403_FORBIDDEN,
            )

        tenant = membership.tenant

        # Bucket creation uses tenant area-mgmt credentials, not end-user keys.
        if not tenant.mgmt_access_key or not tenant.mgmt_secret_key:
            return Response(
                {"error": f"Bucket operations not yet available for {tenant.code}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = BucketCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        project_id = serializer.validated_data["name"]
        if not PROJECT_ID_RE.match(project_id):
            return Response(
                {
                    "error": "Project ID must be 2-50 chars: lowercase letters, numbers, hyphens. Cannot start/end with hyphen."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # NFFADI bucket names include the operational unit to preserve provenance.
        if tenant.code == "NFFADI" and not membership.uo_code:
            return Response(
                {
                    "error": "UO code not set for your account. Contact your administrator to set it in the admin panel."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # S3 bucket names are immutable, so derive them from stable display identity.
        raw_prefix = request.user.display_username or membership.ceph_username
        if not raw_prefix:
            return Response(
                {
                    "error": "Cannot determine username for bucket naming. Contact administrator."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        ceph_user = re.sub(r"[^a-z0-9-]", "-", raw_prefix.lower())
        ceph_user = re.sub(r"-{2,}", "-", ceph_user).strip("-")
        if membership.uo_code:
            uo_safe = re.sub(r"[^a-z0-9-]", "-", membership.uo_code.lower())
            uo_safe = re.sub(r"-{2,}", "-", uo_safe).strip("-")
            bare_name = f"{ceph_user}-{uo_safe}-{project_id}"
        else:
            bare_name = f"{ceph_user}-{project_id}"

        if len(bare_name) > 255:
            return Response(
                {"error": "Generated bucket name too long"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if (
            not re.match(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$", bare_name)
            or len(bare_name) < 3
        ):
            return Response(
                {"error": f'Generated bucket name "{bare_name}" is invalid for S3'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if Bucket.objects.filter(name=bare_name, tenant=tenant).exists():
            return Response(
                {"error": f"Bucket '{bare_name}' already exists"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            s3_create_bucket(tenant, bare_name)
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "BucketAlreadyExists":
                return Response(
                    {"error": "Bucket already exists in S3"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            return Response(
                {"error": f"S3 error: {e}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        try:
            bucket = Bucket.objects.create(
                name=bare_name,
                tenant=tenant,
                owner=request.user,
                bucket_type=Bucket.LOCAL,
                is_deletable=True,
                description=serializer.validated_data.get("description", ""),
                display_name=project_id,
            )
        except Exception:
            # Keep S3 and DB in sync if local metadata creation fails.
            try:
                s3_delete_bucket(tenant, bare_name)
            except Exception:
                logger.error(f"Orphan S3 bucket: {bare_name} (manual cleanup needed)")
            return Response(
                {"error": "Failed to create bucket record"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        BucketPermission.objects.create(
            bucket=bucket,
            user=request.user,
            permission="owner",
            source="local",
        )

        return Response(
            BucketSerializer(bucket, context={"user": request.user}).data,
            status=status.HTTP_201_CREATED,
        )

    def retrieve(self, request, pk=None):
        """Bucket detail with file listing."""
        try:
            bucket = Bucket.objects.select_related("tenant", "owner").get(id=pk)
        except Bucket.DoesNotExist:
            return Response(
                {"error": "Bucket not found"}, status=status.HTTP_404_NOT_FOUND
            )

        if not perms.can_view_bucket(request.user, bucket):
            return Response(
                {"error": "Access denied"}, status=status.HTTP_403_FORBIDDEN
            )

        files = []
        try:
            s3 = get_mgmt_s3_client(bucket.tenant)
            files = list_objects(s3, bucket.name)
        except Exception as e:
            logger.warning(f"Could not list objects in {bucket.name}: {e}")

        # Upload records preserve per-file ownership in shared local buckets.
        upload_records = {
            r.file_key: r.uploaded_by.display_name if r.uploaded_by else None
            for r in FileUploadRecord.objects.filter(bucket=bucket).select_related(
                "uploaded_by"
            )
        }
        for f in files:
            f["uploaded_by"] = upload_records.get(f["key"])

        bucket_data = BucketSerializer(bucket, context={"user": request.user}).data
        bucket_data["files"] = FileSerializer(files, many=True).data

        return Response(bucket_data)

    def destroy(self, request, pk=None):
        """Delete a local research bucket (owner only)."""
        try:
            bucket = Bucket.objects.select_related("tenant").get(id=pk)
        except Bucket.DoesNotExist:
            return Response(
                {"error": "Bucket not found"}, status=status.HTTP_404_NOT_FOUND
            )

        if not perms.can_delete_bucket(request.user, bucket):
            if not bucket.is_deletable:
                return Response(
                    {"error": "Proposal buckets cannot be deleted"},
                    status=status.HTTP_403_FORBIDDEN,
                )
            return Response(
                {"error": "Only the owner can delete this bucket"},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            s3_delete_bucket(bucket.tenant, bucket.name)
        except ClientError as e:
            return Response(
                {"error": f"S3 error: {e}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        bucket.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"])
    def upload(self, request, pk=None):
        """Upload file to bucket."""
        try:
            bucket = Bucket.objects.select_related("tenant").get(id=pk)
        except Bucket.DoesNotExist:
            return Response(
                {"error": "Bucket not found"}, status=status.HTTP_404_NOT_FOUND
            )

        if not perms.can_upload_file(request.user, bucket):
            return Response(
                {"error": "No write permission"}, status=status.HTTP_403_FORBIDDEN
            )

        serializer = FileUploadSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        file_obj = serializer.validated_data["file"]
        file_key = serializer.validated_data.get("key", file_obj.name)

        # Read once; uploaded file streams are consumed after .read().
        file_bytes = file_obj.read()
        content_type = file_obj.content_type or "application/octet-stream"

        try:
            s3 = get_mgmt_s3_client(bucket.tenant)
            upload_object(
                s3, bucket.name, file_key, file_bytes, content_type=content_type
            )
        except Exception as e:
            logger.error(f"Upload failed for {bucket.name}/{file_key}: {e}")
            return Response(
                {"error": f"Upload failed: {e}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        FileUploadRecord.objects.update_or_create(
            bucket=bucket,
            file_key=file_key,
            defaults={"uploaded_by": request.user},
        )

        return Response(
            {"message": f"File '{file_key}' uploaded", "key": file_key},
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["delete"], url_path="files/(?P<file_key>.+)")
    def delete_file(self, request, pk=None, file_key=None):
        """Delete file from bucket."""
        try:
            bucket = Bucket.objects.select_related("tenant").get(id=pk)
        except Bucket.DoesNotExist:
            return Response(
                {"error": "Bucket not found"}, status=status.HTTP_404_NOT_FOUND
            )

        if not perms.can_delete_file(request.user, bucket, file_key):
            return Response(
                {"error": "Cannot delete this file (not owner or not your upload)"},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            s3 = get_mgmt_s3_client(bucket.tenant)
            delete_object(s3, bucket.name, file_key)
        except Exception as e:
            logger.error(f"Delete failed for {bucket.name}/{file_key}: {e}")
            return Response(
                {"error": f"Delete failed: {e}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        FileUploadRecord.objects.filter(bucket=bucket, file_key=file_key).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["get"], url_path="download/(?P<file_key>.+)")
    def download(self, request, pk=None, file_key=None):
        """Download file from bucket."""
        try:
            bucket = Bucket.objects.select_related("tenant").get(id=pk)
        except Bucket.DoesNotExist:
            return Response(
                {"error": "Bucket not found"}, status=status.HTTP_404_NOT_FOUND
            )

        if not perms.can_download_file(request.user, bucket):
            return Response(
                {"error": "Access denied"}, status=status.HTTP_403_FORBIDDEN
            )

        try:
            s3 = get_mgmt_s3_client(bucket.tenant)
            body, content_type = download_object(s3, bucket.name, file_key)
        except Exception as e:
            logger.error(f"Download failed for {bucket.name}/{file_key}: {e}")
            return Response(
                {"error": f"Download failed: {e}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        safe_filename = (
            file_key.split("/")[-1]
            .replace('"', "_")
            .replace("\n", "_")
            .replace("\r", "_")
            or "download"
        )
        response = HttpResponse(body, content_type=content_type)
        response["Content-Disposition"] = f'attachment; filename="{safe_filename}"'
        response["Content-Length"] = len(body)
        return response

    @action(detail=True, methods=["get"], url_path="access-list")
    def access_list(self, request, pk=None):
        """Who has access to this bucket — visible to any user with permission."""
        try:
            bucket = Bucket.objects.select_related("tenant", "owner").get(id=pk)
        except Bucket.DoesNotExist:
            return Response(
                {"error": "Bucket not found"}, status=status.HTTP_404_NOT_FOUND
            )

        if not perms.can_view_bucket(request.user, bucket):
            return Response(
                {"error": "Access denied"}, status=status.HTTP_403_FORBIDDEN
            )

        permissions = (
            BucketPermission.objects.filter(
                bucket=bucket,
            )
            .select_related("user")
            .order_by("-permission", "user__username")
        )

        # Placeholder users may not have a useful display name until first login.
        ceph_map = {}
        if bucket.tenant_id:
            ceph_map = {
                m["user_id"]: m["ceph_username"]
                for m in TenantMembership.objects.filter(
                    tenant_id=bucket.tenant_id,
                    is_active=True,
                ).values("user_id", "ceph_username")
            }

        def best_display(user):
            return user.display_username or ceph_map.get(user.id) or user.display_name

        # Proposal buckets are tenant-owned; local buckets are user-owned.
        if bucket.bucket_type == "local" and bucket.owner:
            owner_label = best_display(bucket.owner)
        else:
            owner_label = bucket.tenant.code if bucket.tenant else None

        user_perm = perms.get_user_permission(request.user, bucket)

        return Response(
            {
                "owner_label": owner_label,
                "is_owner": user_perm == "owner",
                "access": [
                    {
                        "user_id": p.user.id,
                        "display_name": best_display(p.user),
                        "email": ""
                        if p.user.email.endswith("@placeholder.local")
                        else p.user.email,
                        "permission": p.permission,
                        "source": p.source,
                    }
                    for p in permissions
                ],
            }
        )

    @action(detail=True, methods=["get", "post", "delete"], url_path="shares")
    def shares(self, request, pk=None):
        """Manage bucket sharing (local research buckets only)."""
        try:
            bucket = Bucket.objects.select_related("tenant").get(id=pk)
        except Bucket.DoesNotExist:
            return Response(
                {"error": "Bucket not found"}, status=status.HTTP_404_NOT_FOUND
            )

        # Proposal permissions come from RGWSquared and must not be modified locally.
        if bucket.bucket_type == "proposal":
            return Response(
                {"error": "Proposal buckets cannot be shared"},
                status=status.HTTP_403_FORBIDDEN,
            )

        user_perm = perms.get_user_permission(request.user, bucket)
        if user_perm != "owner":
            return Response(
                {"error": "Only bucket owner can manage shares"},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Placeholder users may not have a useful display name until first login.
        ceph_map = {}
        if bucket.tenant_id:
            ceph_map = {
                m["user_id"]: m["ceph_username"]
                for m in TenantMembership.objects.filter(
                    tenant_id=bucket.tenant_id,
                    is_active=True,
                ).values("user_id", "ceph_username")
            }

        def best_display(user):
            return user.display_username or ceph_map.get(user.id) or user.display_name

        if request.method == "GET":
            shares = (
                BucketPermission.objects.filter(
                    bucket=bucket,
                    source="local",
                )
                .select_related("user")
                .exclude(user=request.user)
            )
            return Response(
                [
                    {
                        "id": s.id,
                        "user_id": s.user.id,
                        "username": s.user.username,
                        "display_name": best_display(s.user),
                        "email": ""
                        if s.user.email.endswith("@placeholder.local")
                        else s.user.email,
                        "permission": s.permission,
                        "granted_at": s.granted_at,
                    }
                    for s in shares
                ]
            )

        if request.method == "POST":
            from storage.models import User

            identifier = request.data.get("username", "").strip()
            permission = request.data.get("permission", "ro")
            if permission not in ("ro", "rw"):
                return Response(
                    {"error": "Permission must be ro or rw"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if not identifier:
                return Response(
                    {"error": "Username or email is required"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Accept the three identifiers admins see in the UI and RGWSquared.
            target_user = None
            if "@" in identifier:
                target_user = User.objects.filter(email=identifier).first()
            if not target_user:
                target_user = User.objects.filter(username=identifier).first()
            if not target_user:
                membership = (
                    TenantMembership.objects.filter(
                        ceph_username=identifier,
                        tenant=bucket.tenant,
                        is_active=True,
                    )
                    .select_related("user")
                    .first()
                )
                if membership:
                    target_user = membership.user
            if not target_user:
                return Response(
                    {"error": f'User "{identifier}" not found'},
                    status=status.HTTP_404_NOT_FOUND,
                )
            if target_user == request.user:
                return Response(
                    {"error": "Cannot share with yourself"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Sharing is tenant-local; cross-tenant grants would bypass isolation.
            if not TenantMembership.objects.filter(
                user=target_user,
                tenant=bucket.tenant,
                is_active=True,
            ).exists():
                return Response(
                    {"error": f'User "{identifier}" is not a member of this tenant'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            share, created = BucketPermission.objects.update_or_create(
                bucket=bucket,
                user=target_user,
                defaults={"permission": permission, "source": "local"},
            )
            return Response(
                {
                    "id": share.id,
                    "username": target_user.username,
                    "display_name": best_display(target_user),
                    "permission": share.permission,
                },
                status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
            )

        if request.method == "DELETE":
            share_id = request.data.get("share_id")
            deleted, _ = (
                BucketPermission.objects.filter(
                    id=share_id,
                    bucket=bucket,
                    source="local",
                )
                .exclude(user=request.user)
                .delete()
            )
            if not deleted:
                return Response(
                    {"error": "Share not found"}, status=status.HTTP_404_NOT_FOUND
                )
            return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["delete"], url_path="leave")
    def leave(self, request, pk=None):
        """Allow a non-owner recipient to leave a shared local bucket."""
        try:
            bucket = Bucket.objects.select_related("tenant").get(id=pk)
        except Bucket.DoesNotExist:
            return Response(
                {"error": "Bucket not found"}, status=status.HTTP_404_NOT_FOUND
            )
        if bucket.owner == request.user:
            return Response(
                {"error": "Owner cannot leave their own bucket"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        deleted, _ = BucketPermission.objects.filter(
            bucket=bucket, user=request.user
        ).delete()
        if not deleted:
            return Response(
                {"error": "You do not have access to this bucket"},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["get"], url_path="nexus-detect/(?P<file_key>.+)")
    def nexus_detect(self, request, pk=None, file_key=None):
        """Detect if a file is a valid NeXus/HDF5 file by checking magic bytes.

        Uses a Range request to read only the first 8 bytes — no h5py needed.
        URL uses nexus-detect/ prefix to avoid conflict with files/ delete action.
        """
        try:
            bucket = Bucket.objects.select_related("tenant").get(id=pk)
        except Bucket.DoesNotExist:
            return Response(
                {"error": "Bucket not found"}, status=status.HTTP_404_NOT_FOUND
            )
        if not perms.can_download_file(request.user, bucket):
            return Response(
                {"error": "Access denied"}, status=status.HTTP_403_FORBIDDEN
            )
        try:
            s3 = get_mgmt_s3_client(bucket.tenant)
            obj = s3.get_object(Bucket=bucket.name, Key=file_key, Range="bytes=0-7")
            header = obj["Body"].read()
            meta = s3.head_object(Bucket=bucket.name, Key=file_key)
            size = meta["ContentLength"]
        except ClientError:
            return Response(
                {"error": "File not found"}, status=status.HTTP_404_NOT_FOUND
            )
        HDF5_MAGIC = b"\x89HDF\r\n\x1a\n"
        filename = file_key.split("/")[-1] or file_key
        return Response(
            {"is_nexus": header == HDF5_MAGIC, "size": size, "filename": filename}
        )
