"""Custom social-auth pipeline for federated user provisioning."""

import logging
from django.db import IntegrityError
from social_core.exceptions import AuthForbidden

logger = logging.getLogger(__name__)


def validate_required_claims(backend, details, response, *args, **kwargs):
    """Require stable federation keys before provisioning a local account."""
    sub = response.get("sub")
    if not sub:
        logger.error(f"OAuth2 response missing 'sub' claim. Response: {response}")
        raise AuthForbidden(
            backend,
            "Identity provider did not provide user ID ('sub' claim). "
            "Please check your OAuth2 provider configuration.",
        )

    email = response.get("email") or details.get("email")
    if not email:
        logger.error(f"OAuth2 response missing 'email' claim. Response: {response}")
        raise AuthForbidden(
            backend,
            "Identity provider did not provide email address. "
            "Please ensure 'email' scope is requested and user has email configured.",
        )

    logger.info(f"OAuth2 claims validated successfully. sub={sub}, email={email}")
    return None


def extract_external_id(backend, details, response, user=None, *args, **kwargs):
    """Store OAuth2 subject as the stable local federation key."""
    external_id = response.get("sub")
    idp_source = backend.name

    logger.info(f"Extracted external_id={external_id}, idp_source={idp_source}")

    details["external_id"] = external_id
    details["idp_source"] = idp_source

    return {"details": details}


def extract_federation_fields(backend, details, response, user=None, *args, **kwargs):
    """Normalize optional identity claims from Authentik/federated sources."""
    institution = (
        response.get("institution")
        or response.get("organization")
        or response.get("o")  # short SAML organization claim
        or ""
    )

    department = response.get("department") or response.get("dept") or ""

    affiliation_raw = (
        response.get("affiliation") or response.get("eduPersonAffiliation") or ""
    )

    affiliation_mapping = {
        "faculty": "faculty",
        "staff": "staff",
        "student": "student",
        "affiliate": "affiliate",
        "member": "affiliate",
        "alum": "affiliate",
        "employee": "staff",
    }
    affiliation_status = affiliation_mapping.get(affiliation_raw.lower(), "")

    # ORCID is unique; use NULL so multiple users can omit it safely.
    orcid = response.get("orcid") or None

    profile_picture_url = response.get("picture") or ""

    details.update(
        {
            "institution": institution,
            "department": department,
            "affiliation_status": affiliation_status,
            "orcid": orcid,
            "profile_picture_url": profile_picture_url,
        }
    )

    logger.info(
        f"Extracted federation fields: institution={institution}, "
        f"department={department}, affiliation={affiliation_status}"
    )

    return {"details": details}


def associate_by_ceph_username(backend, details, response, user=None, *args, **kwargs):
    """Match login to sync-created user via preferred_username == ceph_username.

    When RGWSquared sync creates a placeholder user, it stores the upstream
    username as ceph_username in TenantMembership. Authentik sends the same value
    as preferred_username. This step bridges the two so the pipeline updates the
    existing user instead of creating a duplicate.
    """
    if user:
        return {"user": user}

    preferred_username = response.get("preferred_username")
    if not preferred_username:
        return None

    from storage.models import TenantMembership

    membership = (
        TenantMembership.objects.filter(
            ceph_username=preferred_username,
            is_active=True,
        )
        .select_related("user")
        .first()
    )

    if membership:
        logger.info(
            f"Matched sync user by ceph_username={preferred_username}: "
            f"user={membership.user.username} (id={membership.user.id})"
        )
        return {"user": membership.user, "is_new": False}

    return None


def associate_by_email(backend, details, response, user=None, *args, **kwargs):
    """Attach OAuth login to an existing email user before creating a duplicate."""
    from django.contrib.auth import get_user_model

    User = get_user_model()

    if user:
        logger.info(f"User already found by social auth: {user.username}")
        return {"user": user}

    email = details.get("email")
    if not email:
        logger.warning("No email in details, cannot associate by email")
        return None

    try:
        existing_user = User.objects.get(email=email)
        logger.info(
            f"Found existing user by email: username={existing_user.username}, "
            f"email={email}. Will associate social auth account with this user."
        )
        return {"user": existing_user}
    except User.DoesNotExist:
        logger.info(f"No existing user found with email={email}, will create new user")
        return None
    except User.MultipleObjectsReturned:
        logger.error(
            f"Multiple users found with email={email}. "
            "This violates unique constraint. Database inconsistent!"
        )
        raise AuthForbidden(
            backend,
            f"Multiple users found with email {email}. Please contact administrator.",
        )


def generate_username_with_fallback(
    backend, details, response, user=None, *args, **kwargs
):
    """Generate a stable local username from OIDC claims without collisions."""
    from django.contrib.auth import get_user_model

    User = get_user_model()

    if user:
        return None

    username = response.get("preferred_username") or response.get("username")

    if not username:
        email = details.get("email", "")
        if email and "@" in email:
            username = email.split("@")[0]

    if not username:
        external_id = details.get("external_id", "")
        username = external_id[:150] if external_id else "user"

    username = username.replace(" ", "_").replace(".", "_").lower()

    original_username = username
    counter = 1
    while User.objects.filter(username=username).exists():
        username = f"{original_username}_{counter}"
        counter += 1

        if counter > 1000:
            import uuid

            username = f"user_{uuid.uuid4().hex[:8]}"
            break

    details["username"] = username
    logger.info(f"Generated username: {username}")

    return {"details": details}


def create_or_update_user(backend, details, response, user=None, *args, **kwargs):
    """Fill federation fields after social-auth creates or finds the user."""
    if not user:
        logger.warning(
            "create_or_update_user called but user is None - pipeline issue?"
        )
        return None

    external_id = details.get("external_id")
    idp_source = details.get("idp_source", "authentik")
    institution = details.get("institution", "")
    department = details.get("department", "")
    affiliation_status = details.get("affiliation_status", "")
    orcid = details.get("orcid", "")
    profile_picture_url = details.get("profile_picture_url", "")

    # Preserve curated profile fields; only fill blanks from the identity provider.
    given_name = response.get("given_name", "")
    family_name = response.get("family_name", "")
    if not user.first_name and given_name:
        user.first_name = given_name
    if not user.last_name and family_name:
        user.last_name = family_name
    if not user.first_name and not user.last_name:
        name = response.get("name", "")
        if name:
            parts = name.split(" ", 1)
            user.first_name = parts[0]
            user.last_name = parts[1] if len(parts) > 1 else ""

    # Federation keys are authoritative on every login.
    user.external_id = external_id
    user.idp_source = idp_source

    if not user.institution and institution:
        user.institution = institution
    if not user.department and department:
        user.department = department
    if not user.affiliation_status and affiliation_status:
        user.affiliation_status = affiliation_status
    if not user.profile_picture_url and profile_picture_url:
        user.profile_picture_url = profile_picture_url

    if not user.orcid and orcid:
        user.orcid = orcid
    elif not user.orcid and orcid is None:
        user.orcid = None

    # Placeholder users from RGWSquared sync get their real OAuth email on first login.
    new_email = details.get("email")
    if new_email and (not user.email or user.email.endswith("@placeholder.local")):
        try:
            user.email = new_email
            user.save()
            logger.info(f"Updated email for user {user.username}: {new_email}")
        except IntegrityError as e:
            logger.warning(f"Cannot set email for {user.username} to {new_email}: {e}")
            user.refresh_from_db()
    else:
        user.save()

    # Display usernames stay stable after first login even if email later changes.
    if (
        not user.display_username
        and user.email
        and "@" in user.email
        and not user.email.endswith("@placeholder.local")
    ):
        from storage.models import User as UserModel

        base = user.email.split("@")[0].lower()
        candidate = base
        counter = 2
        while (
            UserModel.objects.filter(display_username=candidate)
            .exclude(id=user.id)
            .exists()
        ):
            candidate = f"{base}-{counter}"
            counter += 1
        user.display_username = candidate
        user.save(update_fields=["display_username"])
        logger.info(f"Set display_username={candidate} for {user.username}")

    logger.info(
        f"User provisioned successfully: username={user.username}, "
        f"external_id={user.external_id}, institution={user.institution}"
    )

    return {"user": user}


def extract_tenant_info(backend, details, response, user=None, *args, **kwargs):
    """Resolve tenant memberships from Authentik groups, then RGWSquared fallback."""
    if not user:
        return None

    from storage.models import GroupTenantMapping, TenantMembership, UOMapping

    # preferred_username is the Ceph subuser name used by RGWSquared.
    ceph_username = response.get("preferred_username") or user.username

    matched_tenants = []

    groups = response.get("groups", [])
    group_matched_tenants = []
    if groups:
        for mapping in GroupTenantMapping.objects.select_related("tenant").filter(
            authentik_group__in=groups, tenant__is_active=True
        ):
            tenant = mapping.tenant
            membership, created = TenantMembership.objects.get_or_create(
                user=user,
                tenant=tenant,
                defaults={
                    "ceph_username": ceph_username,
                    "role": "rw",
                },
            )
            if created:
                logger.info(
                    f"Created TenantMembership from group: {user.username} → {tenant.code}"
                )
            matched_tenants.append(tenant.code)
            group_matched_tenants.append(tenant)

    # Staff may match by Authentik group and still own RGWSquared proposal buckets.
    if group_matched_tenants:
        _sync_group_user_buckets(user, ceph_username, group_matched_tenants)

    if not matched_tenants:
        matched_tenants = _rgwsquared_tenant_lookup(user, ceph_username)

    # UO codes drive NFFADI local bucket names.
    if user.institution:
        for membership in TenantMembership.objects.filter(user=user, is_active=True):
            if not membership.uo_code:
                uo = UOMapping.objects.filter(
                    tenant=membership.tenant,
                    institution_name__icontains=user.institution,
                ).first()
                if uo:
                    membership.uo_code = uo.uo_code
                    membership.save(update_fields=["uo_code"])
                    logger.info(
                        f"Set uo_code={uo.uo_code} for {user.username} in {membership.tenant.code}"
                    )

    if matched_tenants:
        logger.info(f"User {user.username} matched tenants: {matched_tenants}")

    return None


def _sync_group_user_buckets(user, ceph_username, tenants):
    """For group-matched users, fetch their RGWSquared buckets and create DB records.

    Staff users (e.g., @areasciencepark.it) match via Authentik groups but may also
    have RGWSquared-managed proposal buckets that need Bucket+BucketPermission records.
    """
    from django.conf import settings
    from storage.services.rgw_squared import RGWSquaredClient

    if not settings.RGWSQUARED_URL or not settings.RGWSQUARED_USERNAME:
        return

    try:
        client = RGWSquaredClient(
            settings.RGWSQUARED_URL,
            settings.RGWSQUARED_USERNAME,
            settings.RGWSQUARED_PASSWORD,
        )
        for tenant in tenants:
            if not tenant.rgwsquared_structure:
                continue
            try:
                users = client.list_users(tenant.rgwsquared_structure)
                if ceph_username not in users:
                    continue
                info = client.get_user_info(tenant.rgwsquared_structure, ceph_username)
                ro_buckets = info.get("ROBuckets", [])
                rw_buckets = info.get("RWBuckets", [])

                from storage.models import TenantMembership

                TenantMembership.objects.filter(user=user, tenant=tenant).update(
                    role="rw" if rw_buckets else "ro",
                )

                bucket_items = client.list_buckets(tenant.rgwsquared_structure)
                _sync_user_buckets_on_login(
                    user, tenant, ro_buckets, rw_buckets, bucket_items=bucket_items
                )
            except Exception as e:
                logger.warning(f"Group user bucket sync failed for {tenant.code}: {e}")
    except Exception as e:
        logger.warning(f"Group user bucket sync failed: {e}")


def _rgwsquared_tenant_lookup(user, ceph_username):
    """Query RGWSquared to find which structures contain this user.

    Called when Authentik group matching finds nothing (external researchers).
    Returns list of matched tenant codes.
    """
    from django.conf import settings
    from storage.models import Tenant, TenantMembership
    from storage.services.rgw_squared import RGWSquaredClient

    if not settings.RGWSQUARED_URL or not settings.RGWSQUARED_USERNAME:
        logger.warning("RGWSquared not configured, skipping tenant lookup")
        return []

    matched = []
    try:
        client = RGWSquaredClient(
            settings.RGWSQUARED_URL,
            settings.RGWSQUARED_USERNAME,
            settings.RGWSQUARED_PASSWORD,
        )

        for tenant in Tenant.objects.filter(is_active=True, code="NFFADI").exclude(
            rgwsquared_structure=""
        ):
            structure = tenant.rgwsquared_structure
            try:
                users = client.list_users(structure)
                if ceph_username not in users:
                    continue

                info = client.get_user_info(structure, ceph_username)
                ro_buckets = info.get("ROBuckets", [])
                rw_buckets = info.get("RWBuckets", [])
                role = "rw" if rw_buckets else "ro"

                membership, created = TenantMembership.objects.get_or_create(
                    user=user,
                    tenant=tenant,
                    defaults={
                        "ceph_username": ceph_username,
                        "role": role,
                    },
                )
                if created:
                    logger.info(
                        f"Created TenantMembership from RGWSquared: "
                        f"{user.username} → {tenant.code} (role={role}, "
                        f"RO={len(ro_buckets)}, RW={len(rw_buckets)})"
                    )
                elif membership.role != role:
                    membership.role = role
                    membership.save(update_fields=["role"])

                bucket_items = client.list_buckets(structure)
                _sync_user_buckets_on_login(
                    user, tenant, ro_buckets, rw_buckets, bucket_items=bucket_items
                )

                matched.append(tenant.code)

            except Exception as e:
                logger.warning(f"RGWSquared lookup failed for {structure}: {e}")
                continue

    except Exception as e:
        logger.warning(f"RGWSquared tenant lookup failed: {e}")

    return matched


def _sync_user_buckets_on_login(
    user, tenant, ro_bucket_names, rw_bucket_names, bucket_items=None
):
    """Create Bucket and BucketPermission records for a user's RGWSquared buckets.

    Called during login to ensure proposal buckets are visible immediately.
    Without this, user logs in but sees empty dashboard because no Django DB records exist.
    """
    from storage.models import Bucket, BucketPermission
    from storage.services.s3_ops import parse_rgwsquared_bucket_name

    auto_bucket_names = None
    if bucket_items is not None:
        auto_bucket_names = set()
        for item in bucket_items:
            if isinstance(item, str):
                auto_bucket_names.add(parse_rgwsquared_bucket_name(item, tenant.code))
            elif item.get("auto"):
                name = item.get("name") or item.get("id")
                if name:
                    auto_bucket_names.add(
                        parse_rgwsquared_bucket_name(str(name), tenant.code)
                    )

    all_buckets = [(name, "rw") for name in rw_bucket_names] + [
        (name, "ro") for name in ro_bucket_names
    ]

    for ms_name, perm in all_buckets:
        bare_name = parse_rgwsquared_bucket_name(ms_name, tenant.code)
        if auto_bucket_names is not None and bare_name not in auto_bucket_names:
            continue

        # Proposal buckets come from RGWSquared and are never locally deletable.
        bucket, created = Bucket.objects.get_or_create(
            name=bare_name,
            tenant=tenant,
            defaults={
                "bucket_type": Bucket.PROPOSAL,
                "is_deletable": False,
                "display_name": bare_name,
            },
        )
        if created:
            logger.info(f"Created proposal Bucket: {tenant.code}/{bare_name}")

        # Never downgrade RW if RGWSquared also reports the bucket as RO.
        existing = BucketPermission.objects.filter(bucket=bucket, user=user).first()
        if not existing:
            BucketPermission.objects.create(
                bucket=bucket,
                user=user,
                permission=perm,
                source="rgwsquared",
            )
        elif existing.permission == "ro" and perm == "rw":
            existing.permission = "rw"
            existing.save(update_fields=["permission"])


def log_user_login(backend, details, response, user=None, *args, **kwargs):
    """
    Log user login for audit trail.

    This is useful for:
    - Security auditing (who logged in when)
    - Debugging OAuth2 issues
    - Compliance requirements

    Args:
        backend: Social auth backend
        details: User details
        response: OAuth2 response
        user: User instance

    Returns:
        None
    """
    if user:
        logger.info(
            f"OAuth2 login successful: user={user.username}, "
            f"external_id={user.external_id}, "
            f"institution={user.institution}, "
            f"email={user.email}, "
            f"idp_source={user.idp_source}"
        )
    else:
        logger.warning("OAuth2 pipeline completed but user is None")

    return None
