"""Configure Authentik OAuth2 provider/application inside the Authentik pod.

Required env vars:
- OIDC_CLIENT_SECRET
- AUTHENTIK_BOOTSTRAP_PASSWORD

Optional env vars:
- OIDC_CLIENT_ID (default: bucket-explorer)
- PUBLIC_APP_URL (default: http://localhost:3000)
"""

import os
import sys
import django

sys.path.append('/')
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "authentik.root.settings")
django.setup()

from authentik.providers.oauth2.models import OAuth2Provider, ClientTypes, ScopeMapping
from authentik.core.models import Application, User
from authentik.crypto.models import CertificateKeyPair
from authentik.flows.models import Flow, FlowStageBinding

def configure():
    print("Starting automated Authentik configuration...")

    PROVIDER_NAME = "bucket-explorer-provider"
    APP_NAME = "Bucket Explorer"
    APP_SLUG = "bucket-explorer"
    CLIENT_ID = os.environ.get("OIDC_CLIENT_ID", "bucket-explorer")
    CLIENT_SECRET = os.environ.get("OIDC_CLIENT_SECRET", "")
    if not CLIENT_SECRET:
        print("Error: OIDC_CLIENT_SECRET is required")
        return False
    public_app_url = os.environ.get("PUBLIC_APP_URL", "http://localhost:3000").rstrip("/")

    REDIRECT_URIS = f"{public_app_url}/api/oauth/complete/authentik/"

    key = CertificateKeyPair.objects.filter(name="authentik Self-signed Certificate").first()
    if not key:
        print("Error: 'authentik Self-signed Certificate' not found.")
        return False
    print(f"Found signing key: {key.name}")

    flow = Flow.objects.filter(slug="default-provider-authorization-implicit-consent").first()
    if not flow:
        flow = Flow.objects.filter(slug="default-provider-authorization-explicit-consent").first()
    if not flow:
        print("Error: Could not find a default authorization flow.")
        return False
    print(f"Found authorization flow: {flow.slug}")

    provider, created = OAuth2Provider.objects.update_or_create(
        name=PROVIDER_NAME,
        defaults={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "client_type": ClientTypes.CONFIDENTIAL,
            "redirect_uris": REDIRECT_URIS,
            "signing_key": key,
            "authorization_flow": flow,
        }
    )

    default_mappings = ScopeMapping.objects.filter(
        managed__startswith="goauthentik.io/providers/oauth2/scope-"
    )
    provider.property_mappings.set(default_mappings)

    action = "Created" if created else "Updated"
    print(f"{action} OAuth2 Provider: {provider.name}")
    print(f"  Client ID: {provider.client_id}")
    print(f"  Scopes: {', '.join(m.scope_name for m in default_mappings)}")
    print(f"  Redirect URIs:\n{provider.redirect_uris}")

    app, app_created = Application.objects.update_or_create(
        slug=APP_SLUG,
        defaults={
            "name": APP_NAME,
            "provider": provider,
            "meta_launch_url": public_app_url,
            "open_in_new_tab": False,
        }
    )
    action = "Created" if app_created else "Updated"
    print(f"{action} Application: {app.name}")

    auth_flow = Flow.objects.filter(slug="default-authentication-flow").first()
    if auth_flow:
        mfa_bindings = FlowStageBinding.objects.filter(
            target=auth_flow,
            stage__name="default-authentication-mfa-validation"
        )
        if mfa_bindings.exists():
            mfa_bindings.delete()
            print("Removed MFA validation stage from authentication flow")
        else:
            print("MFA validation stage already removed")

    BOOTSTRAP_PASSWORD = os.environ.get("AUTHENTIK_BOOTSTRAP_PASSWORD", "")
    if not BOOTSTRAP_PASSWORD:
        print("Error: AUTHENTIK_BOOTSTRAP_PASSWORD is required")
        return False
    try:
        admin = User.objects.get(username="akadmin")
        admin.set_password(BOOTSTRAP_PASSWORD)
        admin.save()
        print("Reset akadmin password")
    except User.DoesNotExist:
        print("Warning: akadmin user not found")

    print("\nAuthentik configuration complete!")
    return True

if __name__ == "__main__":
    success = configure()
    if not success:
        sys.exit(1)
