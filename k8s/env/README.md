# Environment overlays for Kubernetes deploy

This directory centralizes all environment-specific values.

## Structure

- `dev/backend-config.yaml` — non-sensitive development config
- `dev/secrets.yaml` — development secret placeholders (replace locally)
- `dev/authentik-service-nodeport.yaml` — dev-only NodePort exposure
- `prod/backend-config.yaml` — production config template (secure defaults)
- `prod/secrets.yaml` — production secret placeholders

## Local override pattern (recommended)

Create local files that are not committed:

- `k8s/env/dev/backend-config.local.yaml`
- `k8s/env/dev/secrets.local.yaml`
- `k8s/env/prod/backend-config.local.yaml`
- `k8s/env/prod/secrets.local.yaml`

When present, deployment script uses `*.local.*` in preference to committed templates.

## Policy

- Never commit plaintext real credentials.
- Commit templates with placeholders only.
- For production use Sealed Secrets, External Secrets, or SOPS-encrypted files.
