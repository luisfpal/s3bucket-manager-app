"""JWT token types for admin panel access."""

from rest_framework_simplejwt.tokens import RefreshToken


class AdminRefreshToken(RefreshToken):
    """Refresh token that marks access tokens as admin-panel scoped."""

    @classmethod
    def for_admin_user(cls, user):
        token = cls.for_user(user)
        token["admin_panel"] = True
        return token
