# RGWSquared API

RGWSquared is the policy service that Buckets Explorer uses to manage Ceph RGW
structures, users, buckets, and bucket permissions. Ceph RGW stores the objects.
RGWSquared owns bucket lifecycle and access policy.

This document is the public, stable webapp-facing contract. It uses dummy
hostnames, users, buckets, and credentials. Replace the placeholders with values
from your deployment environment.

## Command Setup

The examples below follow one pattern:

- set `BASE` once,
- authenticate once into `TOKEN`,
- write each response body to `/tmp/rgw_resp.json`,
- print the HTTP status on its own line,
- inspect the JSON with `jq`.

```bash
BASE="https://rgwsquared.example.org"
RGWSQUARED_USER="bucket-explorer-service"
RGWSQUARED_CREDENTIAL="<service-login-credential>"
```

## Authentication

All RGWSquared calls require a bearer token. The token is returned in a response
header, not in the JSON body.

```bash
TOKEN=$(curl -s -D - -X POST \
  -H "Content-Type: application/json" \
  -d '{"username":"bucket-explorer-service","pass\u0077ord":"<service-login-credential>"}' \
  "$BASE/auth/login" \
  | grep -i "^x-arkitech-auth-token:" | grep -v expiration | awk '{print $2}' | tr -d '\r')

echo "TOKEN acquired"
```

Expected relevant response headers:

```text
HTTP/1.1 200 OK
x-arkitech-auth-token: <bearer-token>
x-arkitech-auth-token-expiration: <expiration-date>
```

Use the token in every later request:

```text
Authorization: Bearer <bearer-token>
Content-Type: application/json
```

The webapp keeps this token in backend memory only. It must never be sent to the
browser.

## Structures

A structure is the RGWSquared tenant namespace used by the storage platform.
Buckets Explorer maps each active Django tenant to one RGWSquared structure.

### structureList

Lists available structures.

```bash
curl -s -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{}' \
  -o /tmp/rgw_resp.json -w "HTTP: %{http_code}\n" \
  "$BASE/s3struct/structureList" ; jq . /tmp/rgw_resp.json
```

Example response:

```json
{
  "execTime": "4ms",
  "req": {
    "ws": "s3struct",
    "verb": "structureList",
    "pars": {}
  },
  "res": ["NFFADI", "ORBIT"]
}
```

### structureInfo

Checks whether a structure is ready for storage operations and returns transient
tenant management S3 credentials when it is ready.

```bash
curl -s -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"structure":"NFFADI"}' \
  -o /tmp/rgw_resp.json -w "HTTP: %{http_code}\n" \
  "$BASE/s3struct/structureInfo" ; jq . /tmp/rgw_resp.json
```

Example initialized response:

```json
{
  "execTime": "32ms",
  "req": {
    "ws": "s3struct",
    "verb": "structureInfo",
    "pars": {
      "structure": "NFFADI"
    }
  },
  "res": {
    "initialized": true,
    "bucketsAuto": 38,
    "bucketsManual": 5,
    "rgwintUser": {
      "uid": "NFFADI$area-mgmt",
      "display_name": "Internal management user",
      "access_key": "<transient-access-key>",
      "secret\u005fkey": "<transient-s3-credential>"
    }
  }
}
```

Rules:

- `initialized: true` means the webapp may perform bucket and file operations.
- `initialized: false` means the webapp must block bucket and file operations.
- The `rgwintUser` credentials are transient runtime credentials. Use them only
  in backend memory to create the S3 client.
- Do not persist these credentials in Django, Kubernetes ConfigMaps, frontend
  state, browser storage, logs, or documentation.

## Users

RGWSquared users are Ceph-facing usernames. In Buckets Explorer they are stored as
`TenantMembership.ceph_username`. They are not necessarily the same as the local
Django username or the user-facing display name.

### userList

Lists users in one structure.

```bash
curl -s -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"structure":"NFFADI"}' \
  -o /tmp/rgw_resp.json -w "HTTP: %{http_code}\n" \
  "$BASE/s3struct/userList" ; jq . /tmp/rgw_resp.json
```

Example response:

```json
{
  "execTime": "20ms",
  "req": {
    "ws": "s3struct",
    "verb": "userList",
    "pars": {
      "structure": "NFFADI"
    }
  },
  "res": [
    "alice.researcher",
    "bob.scientist",
    "carla.operator"
  ]
}
```

### userInfo

Returns one user's bucket permissions and user-level S3 credentials. Bucket
Explorer uses the RO/RW bucket lists to refresh its local permission cache. It
does not persist user-level S3 credentials.

```bash
curl -s -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"structure":"NFFADI","user":"alice.researcher"}' \
  -o /tmp/rgw_resp.json -w "HTTP: %{http_code}\n" \
  "$BASE/s3struct/userInfo" ; jq . /tmp/rgw_resp.json
```

Example response:

```json
{
  "execTime": "45ms",
  "req": {
    "ws": "s3struct",
    "verb": "userInfo",
    "pars": {
      "structure": "NFFADI",
      "user": "alice.researcher"
    }
  },
  "res": {
    "uid": "NFFADI$ext-users:alice.researcher",
    "access_key": "<user-access-key>",
    "secret\u005fkey": "<user-s3-credential>",
    "ROBuckets": ["NFFADI:proposal-001"],
    "RWBuckets": []
  }
}
```

Permission rule:

- a non-empty `RWBuckets` list makes the user write-capable for that structure,
- otherwise the user is read-only for that structure,
- RO and RW bucket references may include a structure reference prefix such as
  `NFFADI:proposal-001`; normalize them to the bare bucket name before S3 object
  operations. This reference prefix is not the physical Ceph tenant prefix.

### userCreate

Creates a manual user in a structure. Buckets Explorer calls this before creating
or sharing a manual bucket when the target user may not already exist in
RGWSquared.

```bash
curl -s -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"structure":"NFFADI","user":"manual.user"}' \
  -o /tmp/rgw_resp.json -w "HTTP: %{http_code}\n" \
  "$BASE/s3struct/userCreate" ; jq . /tmp/rgw_resp.json
```

Example response:

```json
{
  "execTime": "80ms",
  "req": {
    "ws": "s3struct",
    "verb": "userCreate",
    "pars": {
      "structure": "NFFADI",
      "user": "manual.user"
    }
  },
  "res": null
}
```

Treat an "already exists" service response as an idempotency signal when the
desired user is already present.

## Buckets

Bucket names sent to RGWSquared are bare names. Do not send `NFFADI/<bucket>` or
`NFFADI:<bucket>` in create, update, or delete calls. RGWSquared applies the
structure or tenant prefix when it creates or reports the physical Ceph bucket.
For operators, that physical name is conceptually `{tenant}-{bare-bucket-name}`;
API clients still send only the bare bucket name.

### bucketList

Lists buckets in a structure. Buckets Explorer uses this to refresh local bucket
metadata instead of listing buckets directly from Ceph.

```bash
curl -s -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"structure":"NFFADI"}' \
  -o /tmp/rgw_resp.json -w "HTTP: %{http_code}\n" \
  "$BASE/s3struct/bucketList" ; jq . /tmp/rgw_resp.json
```

Example response:

```json
{
  "execTime": "18ms",
  "req": {
    "ws": "s3struct",
    "verb": "bucketList",
    "pars": {
      "structure": "NFFADI"
    }
  },
  "res": [
    {
      "name": "proposal-001",
      "auto": true,
      "manual": false
    },
    {
      "name": "alice-researcher-project-a",
      "auto": false,
      "manual": true
    }
  ]
}
```

### bucketCreate

Creates a manual bucket and applies its initial permissions.

```bash
curl -s -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"structure":"NFFADI","bucketName":"alice-researcher-project-a","bucketAttributes":{"RWPermissions":["alice.researcher"],"ROPermissions":[]}}' \
  -o /tmp/rgw_resp.json -w "HTTP: %{http_code}\n" \
  "$BASE/s3struct/bucketCreate" ; jq . /tmp/rgw_resp.json
```

Example response:

```json
{
  "execTime": "120ms",
  "req": {
    "ws": "s3struct",
    "verb": "bucketCreate",
    "pars": {
      "structure": "NFFADI",
      "bucketName": "alice-researcher-project-a"
    }
  },
  "res": null
}
```

Rules:

- The owner must be included in `RWPermissions`.
- Every user in `RWPermissions` or `ROPermissions` must already exist in the
  structure.
- After success, do not run any extra Ceph synchronization step. RGWSquared owns
  that lifecycle.
- Create Django metadata only after RGWSquared succeeds.

### bucketUpdate

Replaces the complete permission state for a manual bucket. Send the full desired
state, not a delta.

```bash
curl -s -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"structure":"NFFADI","bucketName":"alice-researcher-project-a","bucketAttributes":{"RWPermissions":["alice.researcher","bob.scientist"],"ROPermissions":["carla.operator"]}}' \
  -o /tmp/rgw_resp.json -w "HTTP: %{http_code}\n" \
  "$BASE/s3struct/bucketUpdate" ; jq . /tmp/rgw_resp.json
```

Example response:

```json
{
  "execTime": "95ms",
  "req": {
    "ws": "s3struct",
    "verb": "bucketUpdate",
    "pars": {
      "structure": "NFFADI",
      "bucketName": "alice-researcher-project-a"
    }
  },
  "res": null
}
```

Buckets Explorer calls RGWSquared first, then persists local sharing metadata.
If local persistence fails after RGWSquared succeeds, refresh local state from
RGWSquared and repair the Django records.

### bucketDelete

Deletes a manual bucket. Buckets Explorer never calls this for proposal buckets.

```bash
curl -s -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"structure":"NFFADI","bucketName":"alice-researcher-project-a"}' \
  -o /tmp/rgw_resp.json -w "HTTP: %{http_code}\n" \
  "$BASE/s3struct/bucketDelete" ; jq . /tmp/rgw_resp.json
```

Example response:

```json
{
  "execTime": "160ms",
  "req": {
    "ws": "s3struct",
    "verb": "bucketDelete",
    "pars": {
      "structure": "NFFADI",
      "bucketName": "alice-researcher-project-a"
    }
  },
  "res": null
}
```

Delete order:

1. call RGWSquared,
2. wait for success (or a definitive already-absent response from RGWSquared),
3. delete Django metadata.

Buckets Explorer does not offer a database-only delete. If `bucketDelete` fails because RGWSquared cannot reach Ceph, fix RGWSquared connectivity and retry — do not remove the Django row alone.

## NFFADI CSV Upload

NFFADI uses a CSV source to map instrument scientists to institutions and
operational units. Buckets Explorer uploads the CSV to RGWSquared and updates its
local UO mapping cache.

```bash
CSV_BASE64=$(base64 -w 0 instruments.csv)

curl -s -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"content\":\"$CSV_BASE64\"}" \
  -o /tmp/rgw_resp.json -w "HTTP: %{http_code}\n" \
  "$BASE/s3structnffadi/csvUpload" ; jq . /tmp/rgw_resp.json
```

Example response:

```json
{
  "execTime": "250ms",
  "req": {
    "ws": "s3structnffadi",
    "verb": "csvUpload",
    "pars": {
      "content": "<base64-csv-content>"
    }
  },
  "res": {
    "uploaded": true,
    "rows": 120
  }
}
```

The webapp then refreshes its local Django cache from `structureInfo`,
`bucketList`, `userList`, and `userInfo`.

## Error Handling

RGWSquared can return useful service messages in non-200 responses. Clients
should preserve those messages instead of replacing them with a generic HTTP
status.

Example error body:

```json
{
  "error": "Bucket already exists"
}
```

Recommended webapp message:

```text
RGWSquared rejected bucketCreate: Bucket already exists
```

Never log bearer tokens, service login credentials, S3 access keys, or S3 secret
material.
