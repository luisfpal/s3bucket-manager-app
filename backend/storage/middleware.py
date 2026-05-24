"""Project middleware for OAuth handoff failures."""

import logging
from urllib.parse import urlencode

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
        return redirect(f"/login?{urlencode({'auth_error': error_code})}")
