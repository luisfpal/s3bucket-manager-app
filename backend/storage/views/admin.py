"""Admin API views — purpose-built React admin backend.

All endpoints require is_staff=True (except admin login).
"""

import base64
import csv
import io
import logging

from django.contrib.auth import authenticate
from django.db.models import Count, Q
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser, AllowAny
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken

from storage.models import (
    Tenant,
    TenantMembership,
    Bucket,
    BucketPermission,
    UOMapping,
    GroupTenantMapping,
)
from storage.services.s3_ops import (
    ensure_structure_initialized,
    get_mgmt_s3_client,
    delete_object,
    get_bucket_stats_for_tenant,
)
from storage.services.sync_service import refresh_local_cache
from storage.services.rgw_squared import RGWSquaredClient, RGWSquaredError

logger = logging.getLogger(__name__)


def _get_sync_client():
    """Create RGWSquaredClient from Django settings."""
    from django.conf import settings

    return RGWSquaredClient(
        base_url=settings.RGWSQUARED_URL,
        username=settings.RGWSQUARED_USERNAME,
        password=settings.RGWSQUARED_PASSWORD,
    )


def _structure_name(tenant):
    return tenant.rgwsquared_structure or tenant.code


def _error_response(message, response_status=status.HTTP_500_INTERNAL_SERVER_ERROR):
    return Response({"error": str(message)}, status=response_status)


@api_view(["POST"])
@permission_classes([AllowAny])
def admin_login(request):
    """Authenticate admin via username/password, return JWT."""
    username = request.data.get("username", "").strip()
    password = request.data.get("password", "")

    if not username or not password:
        return Response(
            {"error": "Username and password required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    user = authenticate(username=username, password=password)
    if not user or not user.is_staff:
        return Response(
            {"error": "Invalid credentials or insufficient privileges"},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    refresh = RefreshToken.for_user(user)
    return Response(
        {
            "access": str(refresh.access_token),
            "refresh": str(refresh),
            "username": user.username,
            "display_name": user.display_name,
        }
    )


def _clean_email(email):
    """Return empty string for placeholder emails."""
    return "" if email.endswith("@placeholder.local") else email


@api_view(["GET"])
@permission_classes([IsAdminUser])
def admin_permissions(request):
    """All bucket permissions — used by BucketsView expanded access."""
    perms = BucketPermission.objects.select_related(
        "user", "bucket", "bucket__tenant"
    ).order_by("-granted_at")

    # (user_id, tenant_id) → ceph_username
    membership_map = {
        (m["user_id"], m["tenant_id"]): m["ceph_username"]
        for m in TenantMembership.objects.filter(is_active=True).values(
            "user_id", "tenant_id", "ceph_username"
        )
    }

    return Response(
        [
            {
                "id": p.id,
                "username": p.user.username,
                "ceph_username": membership_map.get(
                    (p.user_id, p.bucket.tenant_id), p.user.username
                ),
                "email": _clean_email(p.user.email),
                "bucket_name": p.bucket.display_name or p.bucket.name,
                "bucket_ceph_name": p.bucket.name,
                "tenant_code": p.bucket.tenant.code if p.bucket.tenant else None,
                "permission": p.permission,
                "source": p.source,
                "granted_at": p.granted_at,
            }
            for p in perms
        ]
    )


@api_view(["GET"])
@permission_classes([IsAdminUser])
def admin_buckets(request):
    """All buckets with storage stats from RGW."""
    try:
        buckets = list(
            Bucket.objects.select_related("tenant", "owner")
            .annotate(
                shares_count=Count(
                    "permissions",
                    filter=~Q(permissions__permission="owner"),
                )
            )
            .order_by("tenant__code", "name")
        )

        stats = {}
        for tenant in Tenant.objects.filter(is_active=True):
            names = [b.name for b in buckets if b.tenant_id == tenant.id]
            stats.update(get_bucket_stats_for_tenant(tenant, names))

        return Response(
            [
                {
                    "id": b.id,
                    "name": b.name,
                    "display_name": b.display_name,
                    "tenant_code": b.tenant.code if b.tenant else None,
                    "bucket_type": b.bucket_type,
                    "owner_name": b.owner.display_name
                    if b.owner
                    else (b.tenant.code if b.tenant else None),
                    "is_deletable": b.is_deletable,
                    "shares_count": b.shares_count,
                    "size_bytes": stats.get(b.name, {}).get("size_bytes", 0),
                    "num_objects": stats.get(b.name, {}).get("num_objects", 0),
                    "created_at": b.created_at,
                }
                for b in buckets
            ]
        )
    except Exception as e:
        logger.error(f"admin_buckets failed: {e}")
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["GET", "DELETE"])
@permission_classes([IsAdminUser])
def admin_bucket_detail(request, bucket_id):
    """Bucket detail or delete. DELETE only if is_deletable=True."""
    try:
        bucket = Bucket.objects.select_related("tenant", "owner").get(id=bucket_id)
    except Bucket.DoesNotExist:
        return Response({"error": "Bucket not found"}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "DELETE":
        if not bucket.is_deletable:
            return Response(
                {"error": "This bucket cannot be deleted"},
                status=status.HTTP_403_FORBIDDEN,
            )
        try:
            client = _get_sync_client()
            ensure_structure_initialized(bucket.tenant, client=client)
            client.delete_bucket(_structure_name(bucket.tenant), bucket.name)
        except RGWSquaredError as e:
            return _error_response(e, status.HTTP_400_BAD_REQUEST)
        except RuntimeError as e:
            return _error_response(e, status.HTTP_503_SERVICE_UNAVAILABLE)
        except Exception as e:
            return _error_response(e, status.HTTP_500_INTERNAL_SERVER_ERROR)
        bucket.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    from storage.services.s3_ops import list_objects

    files = []
    try:
        s3 = get_mgmt_s3_client(bucket.tenant)
        files = list_objects(s3, bucket.name)
    except Exception as e:
        logger.warning(f"Could not list objects in {bucket.name}: {e}")

    return Response(
        {
            "id": bucket.id,
            "name": bucket.name,
            "display_name": bucket.display_name,
            "tenant_code": bucket.tenant.code if bucket.tenant else None,
            "bucket_type": bucket.bucket_type,
            "owner_name": bucket.owner.display_name
            if bucket.owner
            else (bucket.tenant.code if bucket.tenant else None),
            "is_deletable": bucket.is_deletable,
            "created_at": bucket.created_at,
            "files": files,
        }
    )


@api_view(["DELETE"])
@permission_classes([IsAdminUser])
def admin_delete_file(request, bucket_id, file_key):
    """Delete a file from any bucket (admin only)."""
    try:
        bucket = Bucket.objects.select_related("tenant").get(id=bucket_id)
    except Bucket.DoesNotExist:
        return Response({"error": "Bucket not found"}, status=status.HTTP_404_NOT_FOUND)

    try:
        ensure_structure_initialized(bucket.tenant)
        s3 = get_mgmt_s3_client(bucket.tenant)
        delete_object(s3, bucket.name, file_key)
    except RGWSquaredError as e:
        return _error_response(e, status.HTTP_400_BAD_REQUEST)
    except RuntimeError as e:
        return _error_response(e, status.HTTP_503_SERVICE_UNAVAILABLE)
    except Exception as e:
        return Response(
            {"error": f"Delete failed: {e}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    from storage.models import FileUploadRecord

    FileUploadRecord.objects.filter(bucket=bucket, file_key=file_key).delete()
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(["GET"])
@permission_classes([IsAdminUser])
def admin_users(request):
    """All users with tenant membership info."""
    try:
        memberships = (
            TenantMembership.objects.select_related("user", "tenant")
            .filter(is_active=True)
            .order_by("tenant__code", "user__username")
        )

        return Response(
            [
                {
                    "id": m.id,
                    "user_id": m.user.id,
                    "ceph_username": m.ceph_username,
                    "display_name": m.user.display_name,
                    "email": _clean_email(m.user.email),
                    "tenant_code": m.tenant.code,
                    "role": m.role,
                    "uo_code": m.uo_code,
                    "last_login": m.user.last_login,
                }
                for m in memberships
            ]
        )
    except Exception as e:
        logger.error(f"admin_users failed: {e}")
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["GET"])
@permission_classes([IsAdminUser])
def admin_tenants(request):
    """All tenants with member/bucket counts and storage."""
    try:
        tenants = (
            Tenant.objects.filter(is_active=True)
            .annotate(
                member_count=Count(
                    "memberships", filter=Q(memberships__is_active=True), distinct=True
                ),
                bucket_count=Count("buckets", distinct=True),
            )
            .order_by("code")
        )

        tenant_storage = {}
        structure_status = {}
        client = _get_sync_client()
        for tenant in tenants:
            tenant_buckets = list(
                Bucket.objects.filter(tenant=tenant).values_list("name", flat=True)
            )
            stats = get_bucket_stats_for_tenant(tenant, tenant_buckets)
            tenant_storage[tenant.code] = sum(
                bucket_stats.get("size_bytes", 0) for bucket_stats in stats.values()
            )
            try:
                info = client.get_structure_info(_structure_name(tenant))
            except Exception:
                info = {}
            structure_status[tenant.code] = {
                "initialized": bool(info.get("initialized")),
                "buckets_auto": info.get("bucketsAuto", 0),
                "buckets_manual": info.get("bucketsManual", 0),
            }

        return Response(
            [
                {
                    "id": t.id,
                    "code": t.code,
                    "name": t.name,
                    "member_count": t.member_count,
                    "bucket_count": t.bucket_count,
                    "storage_bytes": tenant_storage.get(t.code, 0),
                    **structure_status.get(t.code, {}),
                }
                for t in tenants
            ]
        )
    except Exception as e:
        logger.error(f"admin_tenants failed: {e}")
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["GET", "POST"])
@permission_classes([IsAdminUser])
def admin_group_mappings(request):
    """List or create group-tenant mappings."""
    if request.method == "GET":
        mappings = GroupTenantMapping.objects.select_related("tenant").order_by(
            "tenant__code"
        )
        return Response(
            [
                {
                    "id": m.id,
                    "authentik_group": m.authentik_group,
                    "tenant_code": m.tenant.code,
                    "tenant_id": m.tenant.id,
                }
                for m in mappings
            ]
        )

    group = request.data.get("authentik_group", "").strip()
    tenant_id = request.data.get("tenant_id")

    if not group or not tenant_id:
        return Response(
            {"error": "authentik_group and tenant_id required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        tenant = Tenant.objects.get(id=tenant_id)
    except Tenant.DoesNotExist:
        return Response({"error": "Tenant not found"}, status=status.HTTP_404_NOT_FOUND)

    if GroupTenantMapping.objects.filter(authentik_group=group, tenant=tenant).exists():
        return Response(
            {"error": f"Mapping {group} : {tenant.code} already exists"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # One Authentik group per tenant keeps tenant selection deterministic.
    existing = GroupTenantMapping.objects.filter(tenant=tenant).first()
    if existing:
        return Response(
            {
                "error": f'Tenant {tenant.code} already mapped to "{existing.authentik_group}". Remove it first.'
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    mapping = GroupTenantMapping.objects.create(authentik_group=group, tenant=tenant)

    return Response(
        {
            "id": mapping.id,
            "authentik_group": mapping.authentik_group,
            "tenant_code": tenant.code,
            "tenant_id": tenant.id,
        },
        status=status.HTTP_201_CREATED,
    )


@api_view(["DELETE"])
@permission_classes([IsAdminUser])
def admin_group_mapping_delete(request, mapping_id):
    """Delete a group-tenant mapping."""
    deleted, _ = GroupTenantMapping.objects.filter(id=mapping_id).delete()
    if not deleted:
        return Response(
            {"error": "Mapping not found"}, status=status.HTTP_404_NOT_FOUND
        )
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(["GET"])
@permission_classes([IsAdminUser])
def admin_available_tenants(request):
    """Structures from RGWSquared, cross-referenced with existing tenants."""
    from django.conf import settings

    try:
        client = RGWSquaredClient(
            base_url=settings.RGWSQUARED_URL,
            username=settings.RGWSQUARED_USERNAME,
            password=settings.RGWSQUARED_PASSWORD,
        )
        structures = client.list_structures()
    except Exception as e:
        return Response(
            {"error": f"RGWSquared error: {e}"}, status=status.HTTP_502_BAD_GATEWAY
        )

    existing = set(Tenant.objects.values_list("code", flat=True))

    data = []
    for s in structures:
        try:
            info = client.get_structure_info(s)
        except Exception:
            info = {}
        data.append(
            {
                "structure": s,
                "has_tenant": s in existing,
                "tenant_id": Tenant.objects.filter(code=s)
                .values_list("id", flat=True)
                .first(),
                "initialized": bool(info.get("initialized")),
                "buckets_auto": info.get("bucketsAuto", 0),
                "buckets_manual": info.get("bucketsManual", 0),
            }
        )
    return Response(data)


@api_view(["GET"])
@permission_classes([IsAdminUser])
def admin_uo_mappings(request):
    """Read-only UO mapping list."""
    mappings = UOMapping.objects.select_related("tenant").order_by(
        "tenant__code", "uo_code"
    )
    return Response(
        [
            {
                "id": m.id,
                "uo_code": m.uo_code,
                "institution_name": m.institution_name,
                "tenant_code": m.tenant.code,
            }
            for m in mappings
        ]
    )


@api_view(["POST"])
@permission_classes([IsAdminUser])
def admin_sync_refresh(request):
    """Refresh local cache for a tenant. Accepts tenant_id or structure_code."""
    structure_code = request.data.get("structure_code")
    tenant_id = request.data.get("tenant_id")

    if not structure_code and not tenant_id:
        return Response(
            {"error": "structure_code or tenant_id required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        if structure_code:
            tenant = Tenant.objects.get(code=structure_code)
        else:
            tenant = Tenant.objects.get(id=tenant_id)
    except Tenant.DoesNotExist:
        return Response({"error": "Tenant not found"}, status=status.HTTP_404_NOT_FOUND)

    if tenant.code != "NFFADI":
        return Response(
            {"error": f"Tenant {tenant.code} is disabled. Only NFFADI is supported."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        stats = refresh_local_cache(tenant)
        if "error" in stats:
            return Response(
                {"error": stats["error"]}, status=status.HTTP_400_BAD_REQUEST
            )
        return Response(stats)
    except Exception as e:
        logger.error(f"Sync refresh failed for {tenant.code}: {e}")
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(["POST"])
@permission_classes([IsAdminUser])
def admin_sync_upload_csv(request):
    """Upload instruments CSV to RGWSquared (csvUpload)."""
    csv_file = request.FILES.get("file")
    if not csv_file:
        return Response(
            {"error": "CSV file required"}, status=status.HTTP_400_BAD_REQUEST
        )

    csv_bytes = csv_file.read()
    csv_base64 = base64.b64encode(csv_bytes).decode()

    try:
        client = _get_sync_client()
        result = client.upload_csv(csv_base64)
    except Exception as e:
        logger.error(f"CSV upload failed: {e}")
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    # The CSV is also the only source that maps RGWSquared usernames to UO codes.
    uo_updated = 0
    try:
        reader = csv.DictReader(io.StringIO(csv_bytes.decode("utf-8")))
        user_institution = {}
        for row in reader:
            username = (row.get("instrument_scientist_username") or "").strip()
            institution = (row.get("institution") or "").strip()
            if username and institution:
                user_institution[username] = institution

        if user_institution:
            tenant = Tenant.objects.filter(code="NFFADI").first()
            if tenant:
                memberships = TenantMembership.objects.filter(
                    tenant=tenant,
                    ceph_username__in=user_institution.keys(),
                    uo_code="",
                ).select_related("tenant")
                for m in memberships:
                    institution = user_institution.get(m.ceph_username, "")
                    if not institution:
                        continue
                    uo = UOMapping.objects.filter(
                        tenant=tenant,
                        institution_name__icontains=institution,
                    ).first()
                    if uo:
                        m.uo_code = uo.uo_code
                        m.save(update_fields=["uo_code"])
                        uo_updated += 1
    except Exception as e:
        logger.warning(f"CSV UO code parsing failed (non-fatal): {e}")

    result["uo_codes_updated"] = uo_updated
    return Response(result)


@api_view(["POST"])
@permission_classes([IsAdminUser])
def admin_sync_update_structure(request):
    """Trigger RGWSquared structureUpdate. Ceph sync is internal to RGWSquared."""
    structure = request.data.get("structure", "NFFADI")
    update_from_ext = request.data.get("update_from_ext", True)

    try:
        client = _get_sync_client()
        result = client.update_structure(structure, update_from_ext=update_from_ext)
        return Response(result)
    except Exception as e:
        logger.error(f"Structure update failed for {structure}: {e}")
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
