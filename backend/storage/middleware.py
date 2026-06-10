"""Project middleware for OAuth handoff failures."""

import logging
from urllib.parse import urlencode

from django.http import HttpResponseBadRequest
from django.shortcuts import redirect
from social_core.exceptions import (
    AuthCanceled,
    AuthFailed,
    AuthForbidden,
    AuthStateForbidden,
    AuthStateMissing,
    SocialAuthBaseException,
)

logger = logging.getLogger(__name__)


class AuthMissingEmailClaim(AuthForbidden):
    """Raised when Authentik provides an empty email claim.

    Distinct from AuthForbidden (group mismatch) so the frontend can show a
    targeted message directing the admin to set the user's email in Authentik.
    """
    pass


class OAuthNextValidationMiddleware:
    """Reject external OAuth next redirects before the Authentik handoff starts."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.rstrip("/") == "/api/oauth/login/authentik":
            next_url = request.GET.get("next", "")
            if next_url and (
                "://" in next_url
                or not next_url.startswith("/")
                or not next_url.startswith("/admin/")
            ):
                return HttpResponseBadRequest("Invalid OAuth redirect target")
        return self.get_response(request)


class OAuthExceptionRedirectMiddleware:
    """Turn social-auth exceptions into a retryable frontend login state."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        if not isinstance(exception, SocialAuthBaseException):
            return None

        error_code = "oauth_failed"
        if isinstance(exception, AuthStateMissing):
            error_code = "oauth_state_missing"
        elif isinstance(exception, AuthStateForbidden):
            error_code = "oauth_state_invalid"
        elif isinstance(exception, AuthMissingEmailClaim):
            error_code = "missing_email"
        elif isinstance(exception, AuthForbidden):
            error_code = "oauth_forbidden"
        elif isinstance(exception, AuthCanceled):
            error_code = "oauth_cancelled"
        elif isinstance(exception, AuthFailed):
            error_code = "oauth_failed"

        logger.warning(
            "OAuth login failed path=%s error=%s detail=%s",
            request.path,
            error_code,
            str(exception),
        )
        session = getattr(request, "session", None)
        next_url = session.get("next", "") if session is not None else ""
        login_path = (
            "/admin/login"
            if isinstance(next_url, str) and next_url.startswith("/admin")
            else "/login"
        )
        return redirect(f"{login_path}?{urlencode({'auth_error': error_code})}")
