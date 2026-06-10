# API Documentation: drf-spectacular and Swagger UI

## Why API Documentation Matters for This Codebase

Buckets Explorer exposes a REST API with around 30 endpoints across authentication, tenant management, bucket operations, file management, and admin workflows. Without interactive documentation, a new developer spends hours reading Django views and serializers to understand what each endpoint expects, what it returns, and how to authenticate.

The API documentation solves this: it gives a developer a live, interactive surface to understand the API, try requests directly from the browser, and export a machine-readable schema for code generation or testing.

---

## Why drf-spectacular

Several tools exist for generating OpenAPI documentation from Django REST Framework. The choice comes down to two candidates: **drf-yasg** (Yet Another Swagger Generator) and **drf-spectacular**.

**drf-yasg** generates OpenAPI 2.0 (Swagger) spec. It has been unmaintained since 2021 and does not support OpenAPI 3.0.

**drf-spectacular** generates **OpenAPI 3.0**, is actively maintained, and has first-class support for DRF's generic views, serializers, and authentication schemes. It handles:

- JWT Bearer authentication declaration
- Separate request/response schemas (`COMPONENT_SPLIT_REQUEST = True`)
- Custom extensions for endpoints that DRF cannot introspect automatically
- Schema path prefix stripping (`SCHEMA_PATH_PREFIX = "/api/"`)
- Fine-grained access control on the schema endpoint itself

The decision is straightforward: drf-spectacular is the maintained standard for DRF + OpenAPI 3.0. drf-yasg is a dead end.

---

## What Gets Generated

drf-spectacular introspects all registered DRF views and serializers to produce an **OpenAPI 3.0 schema** in JSON format. From that schema it serves two UI renderers:

| Renderer | Description |
|----------|-------------|
| **Swagger UI** | Interactive browser UI: try endpoints, see request/response formats, authorize with tokens |
| **ReDoc** | Static, well-structured reference documentation; better for reading, not for trying requests |

The raw schema (machine-readable JSON) is also exposed directly for tools that consume OpenAPI specs (Postman, code generators, contract testing).

---

## Endpoints

| URL | What it serves |
|-----|----------------|
| `GET /api/schema/` | Raw OpenAPI 3.0 schema (JSON) |
| `GET /api/docs/` | Swagger UI (interactive) |
| `GET /api/redoc/` | ReDoc (read-only reference) |

These URLs are registered unconditionally in `urls.py`. Access control is enforced by drf-spectacular's `SERVE_PERMISSIONS` setting — the URLs are always routable, but the response is gated by permissions.

---

## Access in Development (DEBUG = True)

When the backend runs with `DJANGO_DEBUG=True` (the dev overlay default), the schema and both UI endpoints are **open to any visitor**. No authentication is required.

```bash
# From a browser or curl, once port-forwards are active:
curl http://localhost:3000/api/docs/     # Swagger UI HTML
curl http://localhost:3000/api/schema/   # Raw JSON schema
```

Open `http://localhost:3000/api/docs/` in your browser. Swagger UI loads with all endpoints visible.

To try authenticated endpoints in Swagger UI:

1. Log in at `/admin/login` via Authentik to get an admin JWT, or trigger the user OAuth2 flow (`GET /api/oauth/login/authentik/`) to get a user JWT via `/api/auth/token/`.
2. Click the **Authorize** button in Swagger UI (lock icon in the top right).
3. Enter `Bearer <your-token>` in the `jwtAuth` field.
4. All subsequent "Try it out" requests include the Authorization header.

**Which token to use:** The admin JWT (`GET /api/admin/auth/token/` after Authentik login at `/admin/login`) is for `GET /api/admin/*` endpoints and for accessing the schema in production. For testing user-facing endpoints (`/api/buckets/`, `/api/files/`, etc.), use the user JWT from the OAuth2 flow instead — those endpoints require a `X-Tenant-ID` header whose value comes from the `tenants` array in the `/api/auth/token/` response. An admin token has no tenant context and user endpoints will reject it with 403.

---

## Access in Production (DEBUG = False)

When the backend runs with `DJANGO_DEBUG=False` (the prod overlay), the `SERVE_PERMISSIONS` setting switches to `rest_framework.permissions.IsAdminUser`. Only users with `is_staff=True` — synced from the Authentik group in `AUTHENTIK_ADMIN_GROUP` — can access the schema and docs endpoints.

**Why this restriction exists:** The full endpoint map is attack-surface intelligence. A public OpenAPI schema tells an attacker the exact URL patterns, parameter types, and response shapes for every endpoint. Restricting it to admins prevents reconnaissance while keeping the documentation available to authorized developers.

### How to access docs in production

Admin API access uses Authentik OAuth with a separate admin-panel JWT:

1. Open `https://<your-production-hostname>/admin/login` and sign in with Authentik.
2. Your Authentik user must belong to the group configured in `AUTHENTIK_ADMIN_GROUP` (default: `buckets-explorer-admin`).
3. After the OAuth callback, the frontend exchanges the session at `GET /api/admin/auth/token/` for admin JWTs.

```bash
# After browser login, exchange the session cookie for an admin JWT (example)
curl -b cookies.txt https://<your-production-hostname>/api/admin/auth/token/
# Response: {"access": "eyJ...", "refresh": "eyJ...", ...}

# Access the schema with the admin token
curl -H "Authorization: Bearer eyJ..." \
  https://<your-production-hostname>/api/schema/
```

For Swagger UI: log in at `/admin/login`, then open `/api/docs/` and click **Authorize** with `Bearer <access-token>`.

---

## Swagger UI Feature Reference

```
┌────────────────────────────────────────────────────────────────────┐
│  Swagger UI                                              [Authorize]│
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ▼ Auth                                                              │
│    POST /api/oauth/login/{backend}/    OAuth2 initiation            │
│    GET  /api/auth/token/               Exchange code → JWT          │
│                                                                      │
│  ▼ Buckets                                                           │
│    GET  /api/buckets/                  List all accessible buckets  │
│    POST /api/buckets/                  Request a new local bucket   │
│    GET  /api/buckets/{id}/files/       List files in a bucket       │
│    POST /api/buckets/{id}/upload/      Upload a file                │
│    ...                                                               │
│                                                                      │
│  ▼ Admin                                                             │
│    GET  /api/admin/auth/token/         Admin OAuth → JWT            │
│    ...                                                               │
│                                                                      │
└────────────────────────────────────────────────────────────────────┘
```

Key features:

- **Try it out**: Click any endpoint → "Try it out" button → fill parameters → "Execute" → see live response including status code, headers, and body.
- **Authorize**: Set Bearer tokens once; all subsequent requests use them.
- **Schema download**: The "Download" link in the header fetches `GET /api/schema/` as a JSON file, importable into Postman or other API clients.
- **Models section**: Scrolling to the bottom shows all request/response schemas with field names, types, and constraints — generated directly from DRF serializers.

---

## drf-spectacular Configuration

The full configuration is in `backend/settings.py` under `SPECTACULAR_SETTINGS`. Key settings:

```python
SPECTACULAR_SETTINGS = {
    "TITLE": "Buckets Explorer API",
    "VERSION": "1.0.0",

    # Separate schemas for request bodies vs. response bodies.
    # Without this, DRF collapses read-only and write-only fields into one schema,
    # which produces incorrect documentation for fields like 'id' (read-only).
    "COMPONENT_SPLIT_REQUEST": True,

    # Strip the /api/ prefix from all path entries in the schema.
    # Produces cleaner paths: /buckets/ instead of /api/buckets/.
    "SCHEMA_PATH_PREFIX": "/api/",

    # Access control: open in dev, is_staff only in production.
    "SERVE_PERMISSIONS": (
        ["rest_framework.permissions.AllowAny"]
        if DEBUG
        else ["rest_framework.permissions.IsAdminUser"]
    ),
}
```

---

## Regenerating the Schema Offline

For CI pipelines, contract testing, or SDK generation, the schema can be exported to a static file without running a server:

```bash
cd backend
python manage.py spectacular --color --file schema.yaml
```

This produces a `schema.yaml` file that is equivalent to `GET /api/schema/` but does not require the server to be running. Useful for diffing schema changes in code review or feeding into type-safe client generators.
