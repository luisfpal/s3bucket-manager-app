from social_core.backends.open_id_connect import OpenIdConnectAuth


class AuthentikOAuth2(OpenIdConnectAuth):
    """
    Authentik OpenID Connect backend.

    NOTE: Do NOT add prompt=login or max_age=0 here - these cause
    an infinite login loop in Authentik 2024.8.x where the authorization
    endpoint re-triggers authentication after successful login.
    """

    name = "authentik"
