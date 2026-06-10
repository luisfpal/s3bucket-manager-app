# Bucket Explorer Maintainer Guide

Bucket Explorer is a tenant-aware web app for using Ceph RGW storage without
asking researchers to know Ceph, S3 policies, or RGWSquared internals. Django
stores the application state that makes the UI usable. RGWSquared owns storage
policy and bucket lifecycle. Ceph RGW stores object data.

## Architecture

```mermaid
flowchart LR
    Browser[Browser]
    Frontend[nginx + React SPA]
    Backend[Django REST API]
    Postgres[(PostgreSQL)]
    Authentik[Authentik OIDC]
    RGWSquared[RGWSquared API]
    Ceph[Ceph RGW]

    Browser --> Frontend
    Frontend -->|/api/*| Backend
    Backend --> Postgres
    Backend --> Authentik
    Backend --> RGWSquared
    Backend --> Ceph
    RGWSquared --> Ceph
```

The browser talks to nginx. nginx serves the React bundle and proxies `/api/*`
to Django. Django authenticates users through Authentik, stores app metadata in
PostgreSQL, asks RGWSquared for tenant and permission state, and uses transient
RGWSquared S3 credentials for object operations in Ceph.

The central boundary is:

- RGWSquared owns bucket lifecycle, bucket permissions, structure readiness, and
  transient S3 credential issuance.
- Django owns webapp metadata, tenant activation state, UI sharing records, file
  upload records, admin views, and local cache records.
- Ceph RGW owns object storage.

### Storage cache and redeploy

Django is a **cache** of RGWSquared policy for UI purposes, not the source of truth for object bytes. After a PostgreSQL wipe (Class C deploy), admin **Sync → Refresh local cache** recreates `Bucket` rows from `bucketList` for admin inventory. Orphan manual buckets (not created via the webapp) are flagged **ORPHAN** in the admin panel only — they receive no `BucketPermission` rows and never appear on user dashboards.

**Orphan prevention:** researchers must create buckets through the webapp (`POST /api/buckets/`), which sets `display_name`, `source=local` owner permission, and RGWSquared policy together. Avoid creating manual buckets directly in RGWSquared. After any database wipe, review **ORPHAN** badges and delete stale buckets promptly.

See [storage-cache-and-redeploy.md](storage-cache-and-redeploy.md) for Class A/B/C checklists.

## Why nginx in the frontend pod

The frontend container runs **nginx**, not Node.js. Vite compiles React at image build time; the running pod only serves static files and proxies API traffic.

nginx has two jobs (see `frontend/nginx.conf`):

1. **Static file server** — serves the compiled SPA. React Router paths (`/dashboard`, `/auth/callback`, `/admin/login`) fall back to `index.html`.
2. **Reverse proxy (BFF)** — forwards `/api/*` to `backend-service:8000`.

**Why same origin matters:** OAuth login sets a Django session cookie. Browsers send cookies only to the same origin. If the UI were on port 3000 and Django on port 8000 as separate origins, the session cookie would not be sent on `/api/auth/token/` and login would fail. nginx makes both appear as one origin (`https://<domain>/`).

```mermaid
sequenceDiagram
    participant Browser
    participant Nginx as nginx_frontend_pod
    participant Django as backend_service

    Browser->>Nginx: GET /dashboard
    Nginx->>Browser: index.html + JS bundle
    Browser->>Nginx: GET /api/oauth/login/authentik/
    Nginx->>Django: proxy /api/oauth/login/authentik/
    Django->>Browser: redirect to Authentik (session cookie set)
    Browser->>Nginx: GET /api/auth/token/ (cookie included)
    Nginx->>Django: proxy /api/auth/token/
    Django->>Browser: JWT for SPA
```

## Identity and Login

The app uses several usernames because each system has a different job.

| Field | Source | Purpose |
| --- | --- | --- |
| `User.external_id` | Authentik OIDC `sub` | Stable federation identity. |
| `User.email` | Authentik OIDC `email` | Human contact identity and first source for display naming. |
| `User.display_username` | Derived from email local part | Stable user-facing name used in the UI, sharing, and local bucket naming. |
| Authentik `preferred_username` | Authentik | Ceph-facing username candidate. |
| `TenantMembership.ceph_username` | Authentik/RGWSquared | Username sent to RGWSquared and represented in Ceph subuser IDs. |

The display username is deliberately separate from the Ceph username. A user
should see a readable name, while the backend still needs the exact RGWSquared
username for policy calls.

Login is tenant-gated. A user can authenticate successfully at Authentik and
still be denied by Bucket Explorer if no activated tenant is eligible. The app
accepts partial multi-tenant login: if a user belongs to several tenants and one
tenant is not ready, login still succeeds for the tenants that are ready.

Tenant eligibility starts with two local checks: the Django tenant exists and is
active, and the user has a matching Authentik group mapping for that tenant.
After that, tenant-specific storage rules decide how much RGWSquared evidence is
required.

For NFFADI, Authentik only proves that the user belongs to the tenant. The only
allowed group is `nffa-di-users`. RGWSquared must also list the user in the
structure, and RO/RW role truth comes from RGWSquared user bucket permissions.

For other tenants, Authentik group mappings carry the initial role. The normal
pattern is:

- `{tenant}-users` for read-write users,
- `{tenant}-ext` for read-only users.

RGWSquared may still refine bucket visibility when data is available.

### RGWSquared user provisioning by tenant type

How a user gains a Ceph account depends on which model the tenant uses.

**NFFADI (and future proposal-based tenants):** Users are pre-provisioned by the
institution or research programme — RGWSquared maintains the authoritative user list. The
login pipeline checks `userList` and rejects login if the user is absent (`required=True`).
Role and bucket access come from RGWSquared `userInfo`. The webapp never creates users in
RGWSquared for these tenants.

**Simple non-NFFADI tenants (GENOME, PHOTON, etc.):** Users arrive through Authentik. The
`GroupTenantMapping` is the access authority (role = `rw` or `ro`). At login, if the user
is not yet present in RGWSquared, the pipeline auto-creates the Ceph account via
`userCreate` (`_sync_rgwsquared_user_access` in `pipeline.py`, `required=False` path). The
user starts with no project buckets; they create local buckets from the UI.

**Code path for `userCreate` (non-NFFADI first login):**

```mermaid
flowchart TD
    Login[User completes Authentik OAuth]
    Pipeline["extract_tenant_info() in pipeline.py"]
    Sync["_sync_rgwsquared_user_access(required=False)"]
    List["RGWSquaredClient.list_users(structure)"]
    Create["RGWSquaredClient.create_user() → POST /s3struct/userCreate"]
    Done[Login succeeds; role from GroupTenantMapping]

    Login --> Pipeline --> Sync --> List
    List -->|user absent| Create --> Done
    List -->|user present| Done
```

Implementation references:

- `storage/pipeline.py` — `_sync_rgwsquared_user_access()` calls `create_user` when `ceph_username not in users` and `required=False`.
- `storage/services/rgw_squared.py` — `create_user()` posts `{"structure", "user"}` to `/s3struct/userCreate`.
- `docs/rgwsquared-api.md` — API shape for `userCreate`.

NFFADI (`required=True`) **never** calls `userCreate`; missing users are rejected at login.

**Admin responsibility:** The `GroupTenantMapping` for every tenant a user belongs to must
be configured **before** the user's first login attempt. If the mapping does not exist at
login time, the pipeline raises `AuthForbidden` and the browser shows "Your Authentik
account is not a member of any registered group." This is a configuration gap, not a code
bug — set up the mapping in the admin Tenants panel first.

**Proposals and project buckets for non-NFFADI tenants — open design decision:**

The current codebase syncs proposal/project bucket records from RGWSquared at login only for
tenants where `_sync_rgwsquared_user_access` returns True and the user has bucket entries in
`userInfo`. For simple non-NFFADI tenants whose users are auto-provisioned and start with no
buckets, this sync produces no records — which is correct for the current use case (local
buckets only).

If a future non-NFFADI tenant needs to expose upstream proposal or project buckets (like
NFFADI does), the maintainer must decide between two approaches:

1. **Adopt the NFFADI model**: set `required=True` for the tenant, require users to be
   pre-provisioned in RGWSquared by an upstream system, and let `_sync_user_buckets_on_login`
   populate bucket records. Suitable when an external system manages the user roster.

2. **Periodic sync model**: keep `required=False` (auto-provision at login) and add an
   explicit admin refresh action that calls `userInfo` for each member and syncs bucket
   access. Suitable when the bucket list changes independently of login events.

Neither path is currently implemented for non-NFFADI tenants. Choosing the wrong model causes
silent bugs: approach 1 without pre-provisioning locks out all users; approach 2 without the
refresh action leaves bucket records perpetually stale. Document the chosen model in this
guide when it is implemented.

## Tenant Activation

The admin Tenants page is the operator workflow for turning an RGWSquared
structure into a usable webapp tenant. A tenant is fully active only when users
can actually enter and work inside that tenant.

The activation checks are sequential:

1. RGWSquared structure is initialized.
2. Local Django tenant record exists.
3. Authentik group mapping exists.
4. UO coverage is ready when the tenant has UO mapping rows.

UO coverage is outcome-based. It counts only active write-capable memberships
(`rw` and `admin`); read-only memberships should not carry UO codes and are
cleaned during login and refresh flows.

The per-tenant `Refresh` button is not cosmetic. It calls the admin refresh API,
which pulls current RGWSquared state into Django using `structureInfo`,
`bucketList`, `userList`, and `userInfo`. Use it after upstream changes, after a
CSV upload, or when the admin panel shows stale members, buckets, storage, or UO
coverage.

## Buckets

Bucket Explorer shows two bucket types.

| Type | Source | Delete behavior | Permission source |
| --- | --- | --- | --- |
| Proposal bucket | RGWSquared upstream project state | Not deletable from Bucket Explorer | RGWSquared |
| Local bucket | Created by a write-capable tenant user | Owner can delete it | Django local sharing pushed to RGWSquared |

Proposal buckets represent official project or collaboration storage. Their
membership comes from upstream research/project records, so the app does not let
users delete or share them locally.

Local buckets are researcher-owned workspaces. A write-capable user creates one
by entering a short project ID. The app derives the generated bucket name, sends
that bare name to RGWSquared, and then stores local metadata. RGWSquared owns
the physical Ceph bucket layer: when it provisions or reports the storage bucket,
the physical name is tenant-prefixed, conceptually
`{tenant}-{generated-bucket-name}`. Owners can share local buckets with tenant
members. Read-only tenant members can
only receive read-only shares. Write access requires a write-capable tenant
membership.

All buckets use the same read/write object permissions:

- `owner` can upload, download, share, and delete the bucket when it is local,
- `rw` can upload and download; shared RW users can delete only their own files,
- `ro` can view and download only.

## Bucket Names

Bucket Explorer deliberately separates user-facing names from storage names. A
researcher does not need to see tenant prefixes, usernames, or UO segments while
working with a bucket. Admins do need those details when debugging storage state.

| Name | Stored in | Meaning |
| --- | --- | --- |
| Display bucket name | `Bucket.display_name` in Django | User-visible project ID, such as `project-a`. |
| Generated bucket name | `Bucket.name` in Django | Bare name the webapp sends to RGWSquared and uses for object operations with tenant-scoped credentials, such as `alice-cnr-iom-ts-project-a`. |
| Physical Ceph bucket name | RGWSquared/Ceph | RGWSquared-managed tenant-prefixed storage name, conceptually `{tenant}-{generated-bucket-name}`. |

Users type only the project ID. The backend generates the bare bucket name stored
in Django so different users can safely choose the same visible project ID
without colliding inside the tenant. RGWSquared then applies the tenant-level
physical prefix when it provisions the bucket in Ceph. The app treats that
prefix as an RGWSquared/Ceph concern; it is not part of the user-visible bucket
name.

For local buckets:

- NFFADI with a UO code: `{display-username}-{uo-code}-{project-id}`
- Other tenants or users without UO naming: `{display-username}-{project-id}`

The generated name is lowercase, hyphen-separated, and S3-safe. Dots and other
unsafe characters become hyphens. The app sends this generated bare name to
RGWSquared. It does not manually prepend the tenant prefix; RGWSquared owns that
physical Ceph naming layer.

Proposal buckets are synced from RGWSquared. Their display name is usually the
bare upstream bucket or project identifier.

User views show `display_name` first. Admin bucket and permissions views expose
both `display_name` and storage identity. The current admin UI shows the
generated name with tenant context, such as `TENANT/generated-name`; when this
is compared with RGWSquared or Ceph operator output, the physical bucket is the
tenant-prefixed form, such as `tenant-generated-name`. This split is
intentional: users get a clean project name, while operators can still trace the
full storage identity.

## File Names and Upload Records

Uploads are renamed before they are written to Ceph. The rename policy makes
file provenance visible from the object key.

| Tenant and bucket type | File key pattern |
| --- | --- |
| NFFADI proposal | `{tenant}-{bucket-display}-{uploader-uo}-{filename}` |
| NFFADI local | `{tenant}-{uploader-uo}-{bucket-display}-{filename}` |
| Other proposal | `{tenant}-{bucket-display}-{filename}` |
| Other local | `{tenant}-{bucket-display}-{filename}` |

The final filename is sanitized to lowercase S3-safe text. The original filename
is not trusted as an object key. For NFFADI, the UO code comes from the uploader,
not from the bucket owner, so shared RW uploads still carry the right governance
signal.

Every upload creates or updates a `FileUploadRecord`. That record lets the app
show who uploaded a file and enforce the shared-bucket deletion rule: bucket
owners can delete any file; shared RW users can delete only files they uploaded.

## Database Model

The database is not a copy of Ceph. It is the state needed to make the UI,
tenant selection, sharing, audit, and admin workflows work.

The table summaries below show Bucket Explorer's application fields. Standard
Django auth columns inherited by the custom user model are omitted for clarity.

### Relationships

| From | Relationship | To | Purpose |
| --- | --- | --- | --- |
| `users` | one-to-many | `tenant_memberships` | A federated account can belong to multiple tenants. |
| `tenants` | one-to-many | `tenant_memberships` | A tenant contains its active and inactive members. |
| `tenants` | one-to-many | `buckets` | Buckets are tenant-scoped. |
| `users` | one-to-many | `buckets` | Local buckets keep the creator as owner; proposal buckets have no owner. |
| `buckets` | one-to-many | `bucket_permissions` | Bucket access is represented per user. |
| `users` | one-to-many | `bucket_permissions` | Users receive `owner`, `rw`, or `ro` bucket permissions. |
| `buckets` | one-to-many | `file_upload_records` | Upload records track object keys and file sizes. |
| `users` | one-to-many | `file_upload_records` | Upload records preserve who uploaded each object. |
| `tenants` | one-to-many | `uo_mappings` | UO mappings assign operational-unit codes for tenants that require them. |
| `tenants` | one-to-many | `group_tenant_mappings` | Authentik groups activate tenant access and initial roles. |
| `tenants` | one-to-many | `file_name_rules` | Filename rules define tenant-specific upload checks. |
| `tenants` | one-to-one | `tenant_documents` | A tenant can expose one Markdown guide to users. |

### Tables

#### `users`

| Column | Meaning |
| --- | --- |
| `id` | Primary key. |
| `username` | Django username, usually derived from the federated identity. |
| `display_username` | Unique stable display name used in UI, sharing, and local bucket naming. |
| `email` | Unique email from the identity provider. |
| `external_id` | Unique OIDC `sub` identifier. |
| `idp_source` | Identity provider label, normally `authentik`. |
| `institution` | Institution claim from the identity provider. |
| `department` | Department claim or profile detail when provided. |
| `affiliation_status` | Optional user status such as faculty, staff, student, affiliate, or guest. |
| `orcid` | Optional unique ORCID researcher identifier. |
| `profile_picture_url` | Optional profile picture URL from the identity provider. |
| `last_idp_sync` | Timestamp of the most recent identity-provider profile sync. |
| `is_approved` | Application-level account gate. |
| `notes` | Admin-only notes about the account. |

Key constraints and indexes: unique `display_username`, `email`, and
`external_id`; indexes on identity and institution fields.

#### `tenants`

| Column | Meaning |
| --- | --- |
| `id` | Primary key. |
| `code` | Unique local tenant code. |
| `name` | Human-readable tenant name. |
| `rgwsquared_structure` | Structure name used for RGWSquared calls. |
| `bucket_name_prefix` | Local naming prefix used by activation/admin workflows. |
| `is_active` | Whether the tenant can be used by the app. |

Key constraints: unique `code`.

#### `tenant_memberships`

| Column | Meaning |
| --- | --- |
| `id` | Primary key. |
| `user_id` | Foreign key to `users`. |
| `tenant_id` | Foreign key to `tenants`. |
| `ceph_username` | RGWSquared/Ceph username for this user in this tenant. |
| `role` | Tenant role: `ro`, `rw`, or `admin`. |
| `uo_code` | Operational-unit code for write-capable memberships when required. |
| `is_active` | Whether this membership can currently be used. |

Key constraints and indexes: unique `(user_id, tenant_id)`; unique active
`(tenant_id, ceph_username)`; index on `(tenant_id, ceph_username)`.

#### `buckets`

| Column | Meaning |
| --- | --- |
| `id` | Primary key. |
| `name` | Generated bare bucket name stored by Django and sent to RGWSquared. |
| `display_name` | User-visible bucket name, usually the project ID. |
| `tenant_id` | Foreign key to `tenants`. |
| `owner_id` | Foreign key to `users`; null for proposal buckets. |
| `bucket_type` | `proposal` for RGWSquared upstream buckets, `local` for user-created buckets. |
| `is_deletable` | False for proposal buckets, true for local buckets unless overridden. |
| `description` | Optional local bucket description. |
| `created_at` | Creation timestamp. |
| `updated_at` | Last update timestamp. |

Key constraints and indexes: unique `(name, tenant_id)`; index on
`(tenant_id, bucket_type)`. RGWSquared maps `name` to the tenant-prefixed
physical bucket in Ceph; users normally see `display_name`.

#### `bucket_permissions`

| Column | Meaning |
| --- | --- |
| `id` | Primary key. |
| `bucket_id` | Foreign key to `buckets`. |
| `user_id` | Foreign key to `users`. |
| `permission` | Bucket permission: `owner`, `rw`, or `ro`. |
| `source` | Permission source: `rgwsquared` or `local`. |
| `granted_at` | Timestamp when the local permission row was created. |

Key constraints: unique `(bucket_id, user_id)`.

#### `file_upload_records`

| Column | Meaning |
| --- | --- |
| `id` | Primary key. |
| `bucket_id` | Foreign key to `buckets`. |
| `file_key` | Final object key written to Ceph. |
| `uploaded_by_id` | Foreign key to `users`; null if the user record is removed. |
| `file_size` | Object size in bytes. |
| `uploaded_at` | Upload timestamp. |

Key constraints: unique `(bucket_id, file_key)`.

#### `uo_mappings`

| Column | Meaning |
| --- | --- |
| `id` | Primary key. |
| `tenant_id` | Foreign key to `tenants`. |
| `institution_name` | Institution label from identity or CSV data. |
| `uo_code` | Operational-unit code used in bucket and file naming. |

Key constraints: unique `(tenant_id, uo_code)`.

#### `group_tenant_mappings`

| Column | Meaning |
| --- | --- |
| `id` | Primary key. |
| `authentik_group` | Unique Authentik group name. |
| `tenant_id` | Foreign key to `tenants`. |
| `role` | Initial role granted by the group: `rw` or `ro`. |

Key constraints: unique `authentik_group`; unique `(tenant_id, role)`.

#### `file_name_rules`

| Column | Meaning |
| --- | --- |
| `id` | Primary key. |
| `tenant_id` | Foreign key to `tenants`. |
| `substring` | Required filename substring for tenant validation. |

Key constraints: unique `(tenant_id, substring)`.

#### `tenant_documents`

| Column | Meaning |
| --- | --- |
| `id` | Primary key. |
| `tenant_id` | One-to-one foreign key to `tenants`. |
| `tab_name` | Label shown in the user navigation. |
| `content` | Markdown source shown to users. |
| `is_visible` | Whether the document tab is visible when content exists. |
| `updated_at` | Last update timestamp. |

Open [`database-schema.html`](database-schema.html) for the standalone visual
schema, or [`database-schema.pdf`](database-schema.pdf) for the rendered PDF
export.

To export the visual schema as a PDF from the repository root:

```bash
google-chrome --headless --disable-gpu --no-sandbox \
  --print-to-pdf=bucket-explorer-database-schema.pdf --print-to-pdf-no-header \
  "file://$PWD/docs/database-schema.html"
```

If `google-chrome` is not installed, run the same command with `chromium` or
`chromium-browser`.

## Views

User-facing views:

- Login starts the Authentik OAuth flow.
- Tenant selection appears when one account has more than one eligible tenant.
- Dashboard lists accessible proposal and local buckets for the active tenant.
- Bucket detail lists objects, upload controls, download actions, inline file
  viewing, and local bucket sharing.
- Profile shows federated identity fields.
- Tenant guide shows per-tenant Markdown documentation when an admin enables it.
- NeXus viewer opens supported scientific data through the specialized viewer.

Admin views:

- Buckets: inspect buckets, permissions, files, and storage totals.
- Users: inspect tenant-scoped memberships and uploaded files.
- UO Mappings: inspect operational-unit mappings.
- Tenants: activate tenants, manage group mappings, and refresh local cache from
  RGWSquared.
- Sync: upload the NFFADI instruments CSV.
- File Rules: configure required filename substrings per tenant.
- Deviations: inspect uploaded files that do not match tenant rules.
- File Formats: inspect file extension distribution and storage use.
- Tenant Docs: create, update, hide, or delete per-tenant Markdown guides.

## Inline File Viewing

Bucket detail can preview several file types directly in the browser. The viewer
supports common text-like formats, CSV tables, Markdown, HTML, PDF, images, Word
documents, and NeXus/HDF5 scientific files through the dedicated NeXus route.

Downloads remain available even when inline preview is not supported.
