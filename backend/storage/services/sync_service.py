"""Sync orchestration between RGWSquared and Django DB.

Main operation:
    refresh_local_cache: Pull users/buckets/permissions from RGWSquared into Django DB

The full sync pipeline (CSV upload → proposals → generate → apply → refresh)
is orchestrated by the frontend via individual admin API endpoints.
"""

import logging

from django.conf import settings
from django.utils import timezone

from storage.models import (
    TenantMembership,
    Bucket,
    BucketPermission,
    User,
)
from storage.services.rgw_squared import RGWSquaredClient
from storage.services.s3_ops import parse_rgwsquared_bucket_name, get_mgmt_s3_client

logger = logging.getLogger(__name__)


def _get_client():
    """Create RGWSquaredClient from Django settings."""
    return RGWSquaredClient(
        base_url=settings.RGWSQUARED_URL,
        username=settings.RGWSQUARED_USERNAME,
        password=settings.RGWSQUARED_PASSWORD,
    )


def refresh_local_cache(tenant, client=None):
    """Sync RGWSquared state into Django DB.

    Fetches user list and bucket list from RGWSquared, then:
    - Creates/updates TenantMembership with S3 credentials and role
    - Creates/updates Bucket records for proposal buckets
    - Creates/updates BucketPermission records from ROBuckets/RWBuckets

    Returns summary dict with counts.
    """
    if tenant.code != "NFFADI":
        return {"error": f"Tenant {tenant.code} is not enabled for sync"}

    if not tenant.rgwsquared_structure:
        return {"error": f"Tenant {tenant.code} has no rgwsquared_structure configured"}

    if client is None:
        client = _get_client()

    structure = tenant.rgwsquared_structure
    stats = {"users_synced": 0, "buckets_synced": 0, "permissions_synced": 0}
    synced_ceph_usernames = set()

    # RGWSquared does not own the Django bucket table; S3 is the source of truth.
    try:
        s3 = get_mgmt_s3_client(tenant)
        s3_response = s3.list_buckets()
        s3_bucket_names = [b["Name"] for b in s3_response.get("Buckets", [])]
    except Exception as e:
        logger.warning(f"Could not list S3 buckets for {tenant.code}: {e}")
        s3_bucket_names = []

    for bare_name in s3_bucket_names:
        bucket, created = Bucket.objects.get_or_create(
            name=bare_name,
            tenant=tenant,
            defaults={
                "bucket_type": Bucket.PROPOSAL,
                "is_deletable": False,
                "display_name": bare_name,
            },
        )
        if not created and bucket.bucket_type == Bucket.PROPOSAL:
            if bucket.display_name != bare_name:
                bucket.display_name = bare_name
                bucket.save(update_fields=["display_name"])
        stats["buckets_synced"] += 1

    ms_users = client.list_users(structure)
    for username in ms_users:
        user_info = client.get_user_info(structure, username)
        if not user_info:
            logger.warning(f"Empty userInfo for {username} in {structure}")
            continue

        synced_ceph_usernames.add(username)

        rw_buckets = user_info.get("RWBuckets", [])
        ro_buckets = user_info.get("ROBuckets", [])
        role = "rw" if rw_buckets else "ro"

        # Authentik may sanitize usernames, so match through membership before creating.
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            membership = (
                TenantMembership.objects.filter(tenant=tenant, ceph_username=username)
                .select_related("user")
                .first()
            )
            if membership:
                user = membership.user
            else:
                # Placeholder users let admins inspect RGWSquared grants before login.
                # The OAuth pipeline later attaches the real identity to this record.
                user = User.objects.create(
                    username=username,
                    email=f"{username}@placeholder.local",
                    external_id=f"ms:{structure}:{username}",
                    is_active=True,
                    is_approved=True,
                )
                logger.info(f"Created placeholder user for {username} in {structure}")
                stats["users_created"] = stats.get("users_created", 0) + 1

        membership, _ = TenantMembership.objects.update_or_create(
            user=user,
            tenant=tenant,
            defaults={
                "ceph_username": username,
                "role": role,
                "s3_access_key": user_info.get("access_key", ""),
                "s3_secret_key": user_info.get("secret_key", ""),
                "credentials_updated_at": timezone.now(),
            },
        )

        # UO codes are required for NFFADI local bucket naming.
        if user.institution and not membership.uo_code:
            from storage.models import UOMapping

            uo = UOMapping.objects.filter(
                tenant=tenant,
                institution_name__icontains=user.institution,
            ).first()
            if uo:
                membership.uo_code = uo.uo_code
                membership.save(update_fields=["uo_code"])
                logger.info(
                    f"Set uo_code={uo.uo_code} for {username} in {tenant.code} during sync"
                )

        stats["users_synced"] += 1

        _sync_user_permissions(user, tenant, rw_buckets, ro_buckets)
        stats["permissions_synced"] += len(rw_buckets) + len(ro_buckets)

    # Remove only RGWSquared-derived access; local sharing is user-managed state.
    stale_memberships = (
        TenantMembership.objects.filter(
            tenant=tenant,
            is_active=True,
        )
        .exclude(
            ceph_username__in=synced_ceph_usernames,
        )
        .exclude(ceph_username="")
    )

    deactivated = 0
    for m in stale_memberships:
        perms_deleted, _ = BucketPermission.objects.filter(
            user=m.user,
            bucket__tenant=tenant,
            source="rgwsquared",
        ).delete()
        m.s3_access_key = ""
        m.s3_secret_key = ""
        m.credentials_updated_at = None
        m.is_active = False
        m.save(
            update_fields=[
                "s3_access_key",
                "s3_secret_key",
                "credentials_updated_at",
                "is_active",
            ]
        )
        logger.info(
            f"Deactivated stale membership: {m.ceph_username} in {tenant.code} ({perms_deleted} perms removed)"
        )
        deactivated += 1

    if deactivated:
        stats["users_deactivated"] = deactivated

    return stats


def _sync_user_permissions(user, tenant, rw_bucket_names, ro_bucket_names):
    """Update BucketPermission records for a user from RGWSquared data.

    Uses update_or_create to avoid race conditions with local sharing.
    Removes stale RGWSquared permissions that are no longer in the source.
    """
    synced_bucket_ids = set()

    for ms_name in rw_bucket_names:
        bare_name = parse_rgwsquared_bucket_name(ms_name, tenant.code)
        try:
            bucket = Bucket.objects.get(name=bare_name, tenant=tenant)
        except Bucket.DoesNotExist:
            logger.warning(f"Bucket {bare_name} not found for permission sync")
            continue
        BucketPermission.objects.update_or_create(
            bucket=bucket,
            user=user,
            source="rgwsquared",
            defaults={"permission": "rw"},
        )
        synced_bucket_ids.add(bucket.id)

    # RW wins if RGWSquared returns the same bucket in both lists.
    for ms_name in ro_bucket_names:
        bare_name = parse_rgwsquared_bucket_name(ms_name, tenant.code)
        try:
            bucket = Bucket.objects.get(name=bare_name, tenant=tenant)
        except Bucket.DoesNotExist:
            logger.warning(f"Bucket {bare_name} not found for permission sync")
            continue
        if bucket.id not in synced_bucket_ids:
            BucketPermission.objects.update_or_create(
                bucket=bucket,
                user=user,
                source="rgwsquared",
                defaults={"permission": "ro"},
            )
            synced_bucket_ids.add(bucket.id)

    BucketPermission.objects.filter(
        user=user,
        bucket__tenant=tenant,
        source="rgwsquared",
    ).exclude(bucket_id__in=synced_bucket_ids).delete()
