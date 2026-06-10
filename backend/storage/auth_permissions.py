"""DRF permissions for admin panel API access."""

from rest_framework.permissions import BasePermission


class AdminPanelPermission(BasePermission):
    """Require staff user with an admin-panel JWT (admin_panel claim).

    Session-authenticated staff (e.g. Swagger after admin OAuth) is allowed when
    no JWT is present — same pattern as production schema access.
    """

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated or not user.is_staff:
            return False

        token = request.auth
        if token is None:
            return True

        if hasattr(token, "get"):
            return token.get("admin_panel") is True
        if isinstance(token, dict):
            return token.get("admin_panel") is True
        return False
