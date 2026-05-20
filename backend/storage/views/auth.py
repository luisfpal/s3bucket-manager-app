"""Auth views: OAuth2 session→JWT bridge + tenant selection."""

import logging

from django.contrib.auth import logout as auth_logout
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken

from storage.models import TenantMembership, UOMapping
from storage.serializers import UserSerializer

logger = logging.getLogger(__name__)


@api_view(["GET"])
@permission_classes([AllowAny])
def exchange_token(request):
    """Exchange OAuth2 session for JWT tokens.

    After OAuth2 completes, React calls this to get JWT tokens.
    Now includes tenant membership info for multi-tenant routing.
    """
    if not request.user.is_authenticated:
        return Response(
            {"error": "No active session. Please login via OAuth2 first."},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    if not request.user.can_access_system:
        return Response(
            {"error": "Account pending approval."},
            status=status.HTTP_403_FORBIDDEN,
        )

    user = request.user
    memberships = TenantMembership.objects.filter(
        user=user, is_active=True
    ).select_related("tenant")

    # The frontend stores one active tenant and sends it back as X-Tenant-ID.
    tenants = [
        {
            "id": m.tenant.id,
            "code": m.tenant.code,
            "name": m.tenant.name,
            "role": m.role,
        }
        for m in memberships
    ]

    active_tenant = None
    if len(tenants) == 1:
        active_tenant = tenants[0]

    refresh = RefreshToken.for_user(user)
    user_data = UserSerializer(user).data

    # JWTs become the API credential; the OAuth2 session ends after handoff.
    auth_logout(request)

    logger.info(
        f"JWT issued for user={user.username}, tenants={[t['code'] for t in tenants]}"
    )

    return Response(
        {
            "user": user_data,
            "tokens": {
                "access": str(refresh.access_token),
                "refresh": str(refresh),
            },
            "tenants": tenants,
            "active_tenant": active_tenant,
            "tenant_selection_required": len(tenants) > 1,
        }
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def select_tenant(request):
    """Select active tenant for multi-tenant users.

    POST /api/auth/select-tenant/
    Body: {"tenant_id": 1}

    Returns updated user info with selected tenant context.
    """
    tenant_id = request.data.get("tenant_id")
    if not tenant_id:
        return Response(
            {"error": "tenant_id is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        membership = TenantMembership.objects.select_related("tenant").get(
            user=request.user,
            tenant_id=tenant_id,
            is_active=True,
        )
    except TenantMembership.DoesNotExist:
        return Response(
            {"error": "You do not have access to this tenant"},
            status=status.HTTP_403_FORBIDDEN,
        )

    return Response(
        {
            "active_tenant": {
                "id": membership.tenant.id,
                "code": membership.tenant.code,
                "name": membership.tenant.name,
                "role": membership.role,
            },
            "membership": {
                "ceph_username": membership.ceph_username,
                "uo_code": membership.uo_code,
            },
        }
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def current_user(request):
    """Get current user info with tenant memberships."""
    user = request.user
    memberships = TenantMembership.objects.filter(
        user=user, is_active=True
    ).select_related("tenant")

    user_data = UserSerializer(user).data

    uo_codes = [m.uo_code for m in memberships if m.uo_code]
    uo_name_map = {}
    if uo_codes:
        uo_name_map = {
            (um.tenant_id, um.uo_code): um.institution_name
            for um in UOMapping.objects.filter(
                tenant__in=[m.tenant_id for m in memberships],
                uo_code__in=uo_codes,
            )
        }

    user_data["tenants"] = [
        {
            "id": m.tenant.id,
            "code": m.tenant.code,
            "name": m.tenant.name,
            "role": m.role,
            "uo_code": m.uo_code,
            "uo_name": uo_name_map.get((m.tenant_id, m.uo_code), ""),
        }
        for m in memberships
    ]
    user_data["is_admin"] = user.is_staff

    return Response(user_data)


@api_view(["GET"])
@permission_classes([AllowAny])
def health_check(request):
    """Health check for Kubernetes probes."""
    return Response({"status": "ok"})
