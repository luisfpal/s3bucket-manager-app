"""Sync orchestration between RGWSquared and Django DB.

Main operation:
    refresh_local_cache: Pull users/buckets/permissions from RGWSquared into Django DB

The admin sync pipeline asks RGWSquared to update its structure, then refreshes
this local cache from RGWSquared's current JSON state.
"""

import logging

from django.conf import settings

from storage.models import (
    TenantMembership,
    Bucket,
    BucketPermission,
    User,
)
from storage.services.rgw_squared import RGWSquaredClient
from storage.services.s3_ops import parse_rgwsquared_bucket_name
from storage.access import is_write_capable

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
    - Creates/updates TenantMembership with role
    - Creates/updates Bucket records for proposal buckets
    - Creates/updates BucketPermission records for RGWSquared auto buckets

    Returns summary dict with counts.
    """
    if not tenant.rgwsquared_structure:
        return {"error": f"Tenant {tenant.code} has no rgwsquared_structure configured"}

    if client is None:
        client = _get_client()

    structure = tenant.rgwsquared_structure
    stats = {"users_synced": 0, "buckets_synced": 0, "permissions_synced": 0}
    synced_ceph_usernames = set()
    proposal_bucket_ids_by_name = {}

    try:
        structure_info = client.get_structure_info(structure)
    except Exception as e:
        logger.warning(f"Could not fetch structureInfo for {tenant.code}: {e}")
        structure_info = {}

    stats["initialized"] = bool(structure_info.get("initialized"))

    try:
        rgw_buckets = client.list_buckets(structure)
    except Exception as e:
        logger.warning(f"Could not list RGWSquared buckets for {tenant.code}: {e}")
        rgw_buckets = []

    for item in rgw_buckets:
        if isinstance(item, str):
            bucket_name = item
            is_auto = True
            is_manual = False
        else:
            bucket_name = item.get("name") or item.get("id")
            is_auto = bool(item.get("auto"))
            is_manual = bool(item.get("manual"))
        if not bucket_name:
            continue

        bare_name = parse_rgwsquared_bucket_name(str(bucket_name), tenant.code)
        bucket_type = Bucket.LOCAL if is_manual and not is_auto else Bucket.PROPOSAL
        bucket, created = Bucket.objects.get_or_create(
            name=bare_name,
            tenant=tenant,
            defaults={
                "bucket_type": bucket_type,
                "is_deletable": bucket_type == Bucket.LOCAL,
                "display_name": bare_name,
            },
        )
        if not created:
            update_fields = []
            if bucket.display_name != bare_name and bucket.bucket_type == Bucket.PROPOSAL:
                bucket.display_name = bare_name
                update_fields.append("display_name")
            if bucket.bucket_type == Bucket.PROPOSAL and bucket.is_deletable:
                bucket.is_deletable = False
                update_fields.append("is_deletable")
            if update_fields:
                bucket.save(update_fields=update_fields)

        if bucket.bucket_type == Bucket.PROPOSAL:
            proposal_bucket_ids_by_name[bare_name] = bucket.id
        stats["buckets_synced"] += 1

    ms_users = client.list_users(structure)
    for username in ms_users:
        synced_ceph_usernames.add(username)
        try:
            user_info = client.get_user_info(structure, username)
        except Exception as e:
            logger.warning(
                f"Could not fetch userInfo for {username} in {structure}: {e}"
            )
            stats["user_errors"] = stats.get("user_errors", 0) + 1
            continue
        if not user_info:
            logger.warning(f"Empty userInfo for {username} in {structure}")
            continue

        user_perms = {
            "rw": _bucket_ids_from_user_info(
                user_info.get("RWBuckets", []), proposal_bucket_ids_by_name, tenant
            ),
            "ro": _bucket_ids_from_user_info(
                user_info.get("ROBuckets", []), proposal_bucket_ids_by_name, tenant
            ),
        }
        role = "rw" if user_perms["rw"] else "ro"

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
                # For email-format ceph_usernames (has "@"): the user may have logged in
                # via OAuth before this tenant was activated, creating a real Django user
                # with email=ceph_username. Reuse it to avoid a unique_active_ceph_username
                # constraint conflict when the pipeline runs update_or_create at next login.
                # Short-format usernames (no "@") are found directly by username above.
                real_user = (
                    User.objects.filter(email=username).first() if "@" in username else None
                )
                if real_user:
                    user = real_user
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
                "is_active": True,
            },
        )

        # UO codes are only for write-capable users; RO users must not carry them.
        if not is_write_capable(membership.role):
            if membership.uo_code:
                membership.uo_code = ""
                membership.save(update_fields=["uo_code"])
                stats["uo_codes_cleared"] = stats.get("uo_codes_cleared", 0) + 1
                logger.info(
                    f"Cleared uo_code for read-only user {username} in {tenant.code} during sync"
                )
        elif user.institution and not membership.uo_code:
            from storage.models import UOMapping

            uo = UOMapping.objects.filter(
                tenant=tenant,
                institution_name__icontains=user.institution,
            ).first()
            if uo:
                membership.uo_code = uo.uo_code
                membership.save(update_fields=["uo_code"])
                stats["uo_codes_updated"] = stats.get("uo_codes_updated", 0) + 1
                logger.info(
                    f"Set uo_code={uo.uo_code} for {username} in {tenant.code} during sync"
                )

        stats["users_synced"] += 1

        synced_count = _sync_user_permissions(
            user, tenant, user_perms["rw"], user_perms["ro"]
        )
        stats["permissions_synced"] += synced_count

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
        m.is_active = False
        m.save(update_fields=["is_active"])
        logger.info(
            f"Deactivated stale membership: {m.ceph_username} in {tenant.code} ({perms_deleted} perms removed)"
        )
        deactivated += 1

    if deactivated:
        stats["users_deactivated"] = deactivated

    return stats


def _bucket_ids_from_user_info(bucket_names, bucket_ids_by_name, tenant):
    """Resolve RGWSquared userInfo bucket references to proposal bucket IDs."""
    bucket_ids = set()
    for name in bucket_names or []:
        bare_name = parse_rgwsquared_bucket_name(str(name), tenant.code)
        bucket_id = bucket_ids_by_name.get(bare_name)
        if bucket_id:
            bucket_ids.add(bucket_id)
    return bucket_ids


def _sync_user_permissions(user, tenant, rw_bucket_ids, ro_bucket_ids):
    """Update BucketPermission records for a user from RGWSquared data.

    Uses update_or_create to avoid race conditions with local sharing.
    Removes stale RGWSquared permissions that are no longer in the source.
    """
    synced_bucket_ids = set()

    for bucket_id in rw_bucket_ids:
        BucketPermission.objects.update_or_create(
            bucket_id=bucket_id,
            user=user,
            source="rgwsquared",
            defaults={"permission": "rw"},
        )
        synced_bucket_ids.add(bucket_id)

    # RW wins if RGWSquared returns the same bucket in both lists.
    for bucket_id in ro_bucket_ids:
        if bucket_id not in synced_bucket_ids:
            BucketPermission.objects.update_or_create(
                bucket_id=bucket_id,
                user=user,
                source="rgwsquared",
                defaults={"permission": "ro"},
            )
            synced_bucket_ids.add(bucket_id)

    BucketPermission.objects.filter(
        user=user,
        bucket__tenant=tenant,
        source="rgwsquared",
    ).exclude(bucket_id__in=synced_bucket_ids).delete()
    return len(synced_bucket_ids)
