"""Helpers for Authentik admin group checks.

Admin token exchange gates on user.is_staff, synced from the OIDC groups claim
during extract_tenant_info. These helpers read persisted groups for auditing;
they are not the primary exchange gate.
"""

from django.conf import settings


def authentik_groups_for_user(user):
    """Return OIDC group names stored for the user's Authentik association."""
    from social_django.models import UserSocialAuth

    try:
        social = UserSocialAuth.objects.get(user=user, provider="authentik")
    except UserSocialAuth.DoesNotExist:
        return []

    groups = social.extra_data.get("groups", [])
    if isinstance(groups, str):
        return [groups]
    return list(groups or [])


def user_in_admin_group(user):
    """True when the user's latest Authentik groups include AUTHENTIK_ADMIN_GROUP."""
    admin_group = settings.AUTHENTIK_ADMIN_GROUP
    if not admin_group:
        return False
    return admin_group in authentik_groups_for_user(user)
