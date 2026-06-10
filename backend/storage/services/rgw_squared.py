"""RGWSquared service client.

All RGWSquared calls go through this one file. If the RGWSquared API changes
or we switch to IAM Accounts, only this file changes.
"""

import logging
import time
import requests

logger = logging.getLogger(__name__)


class RGWSquaredError(RuntimeError):
    """User-facing RGWSquared failure message."""


def rgw_bucket_already_absent(error) -> bool:
    """True when RGWSquared reports the bucket is already gone from policy/storage."""
    err_str = str(error).lower()
    return (
        "not found" in err_str
        or "does not exist" in err_str
        or "null" in err_str
    )


def delete_bucket_via_rgw(client, structure, bucket_name):
    """Delete through RGWSquared; tolerate already-absent buckets after the RGW call."""
    try:
        client.delete_bucket(structure, bucket_name)
    except RGWSquaredError as exc:
        if rgw_bucket_already_absent(exc):
            logger.warning(
                "Bucket %s already absent in RGWSquared (%s); proceeding with DB cleanup",
                bucket_name,
                exc,
            )
            return
        raise


class RGWSquaredClient:
    """Thin wrapper around the RGWSquared REST API."""

    TOKEN_REFRESH_BUFFER = 300  # refresh before RGWSquared's 8-hour token expires

    def __init__(self, base_url, username, password):
        self.base_url = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._token = None
        self._token_expires_at = 0

    def _ensure_auth(self):
        """Auto-refresh JWT if expired or missing."""
        if self._token and time.time() < self._token_expires_at:
            return

        resp = requests.post(
            f"{self.base_url}/auth/login",
            json={"username": self._username, "password": self._password},
            timeout=15,
        )
        resp.raise_for_status()

        self._token = resp.headers.get("x-arkitech-auth-token")
        if not self._token:
            raise RuntimeError("RGWSquared /auth/login did not return a token")

        self._token_expires_at = time.time() + (8 * 3600) - self.TOKEN_REFRESH_BUFFER
        logger.info("RGWSquared token refreshed")

    def _extract_error(self, path, resp):
        """RGWSquared returns every application failure as HTTP 500."""
        body = (resp.text or "").strip()
        if body:
            try:
                data = resp.json()
            except requests.exceptions.JSONDecodeError:
                return body[:500]
            if isinstance(data, dict):
                for key in ("message", "error", "err"):
                    if data.get(key):
                        return str(data[key])
                if data.get("res"):
                    return str(data["res"])
            return str(data)[:500]
        return f"RGWSquared {path} failed with HTTP {resp.status_code}"

    def _post(self, path, payload=None, timeout=30):
        """Make authenticated POST to RGWSquared. Returns parsed JSON response."""
        self._ensure_auth()
        resp = requests.post(
            f"{self.base_url}{path}",
            json=payload or {},
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )
        if resp.status_code != 200:
            raise RGWSquaredError(self._extract_error(path, resp))

        # RGWSquared may return plain-text error bodies.
        try:
            data = resp.json()
        except requests.exceptions.JSONDecodeError:
            raise RGWSquaredError(
                f"RGWSquared {path} returned non-JSON: {resp.text[:200]}"
            )

        return data

    def list_structures(self):
        """Returns list of structure names, e.g. ["NFFADI"]."""
        data = self._post("/s3struct/structureList")
        return data.get("res", [])

    def get_structure_info(self, structure):
        """Returns structure readiness and area-mgmt credentials."""
        data = self._post("/s3struct/structureInfo", {"structure": structure})
        return data.get("res", {})

    def update_structure(self, structure=None, update_from_ext=True):
        """Refresh structure JSON and let RGWSquared sync Ceph internally."""
        payload = {"updateFromExt": bool(update_from_ext)}
        if structure:
            payload["structure"] = structure
        return self._post("/s3struct/structureUpdate", payload, timeout=120)

    def list_users(self, structure):
        """Returns list of usernames in a structure."""
        data = self._post("/s3struct/userList", {"structure": structure})
        return data.get("res", [])

    def create_user(self, structure, user):
        """Create a manual user in the structure."""
        return self._post(
            "/s3struct/userCreate",
            {"structure": structure, "user": user},
            timeout=60,
        )

    def get_user_info(self, structure, user):
        """Returns {uid, access_key, secret_key, ROBuckets, RWBuckets}.

        ROBuckets/RWBuckets contain strings like "NFFADI:275".
        Strip the "NFFADI:" prefix to get the bare bucket name for S3 ops.
        """
        data = self._post("/s3struct/userInfo", {"structure": structure, "user": user})
        return data.get("res", {})

    def list_buckets(self, structure, auto=None, manual=None):
        """Returns list of bucket names (may include non-tenant buckets)."""
        payload = {"structure": structure}
        if auto is not None:
            payload["auto"] = bool(auto)
        if manual is not None:
            payload["manual"] = bool(manual)
        data = self._post("/s3struct/bucketList", payload)
        return data.get("res", [])

    def get_bucket_info(self, structure, bucket_name):
        """Returns bucket metadata including RO/RW permissions and tags."""
        data = self._post(
            "/s3struct/bucketInfo",
            {
                "structure": structure,
                "bucketName": bucket_name,
            },
        )
        return data.get("res", {})

    def check_bucket_name(self, structure, bucket_name):
        """Ask RGWSquared whether a bucket name is acceptable/available."""
        data = self._post(
            "/s3struct/bucketCheckName",
            {"structure": structure, "bucketName": bucket_name},
        )
        return data.get("res")

    def create_bucket(
        self, structure, bucket_name, rw_permissions=None, ro_permissions=None, tags=None
    ):
        """Create a manual bucket and immediately provision it in Ceph."""
        return self._post(
            "/s3struct/bucketCreate",
            {
                "structure": structure,
                "bucketName": bucket_name,
                "bucketAttributes": {
                    "RWPermissions": rw_permissions or [],
                    "ROPermissions": ro_permissions or [],
                    **({"tags": tags} if tags else {}),
                },
            },
            timeout=90,
        )

    def update_bucket(
        self, structure, bucket_name, rw_permissions=None, ro_permissions=None, tags=None
    ):
        """Replace manual bucket permissions and immediately sync the bucket."""
        return self._post(
            "/s3struct/bucketUpdate",
            {
                "structure": structure,
                "bucketName": bucket_name,
                "bucketAttributes": {
                    "RWPermissions": rw_permissions or [],
                    "ROPermissions": ro_permissions or [],
                    **({"tags": tags} if tags else {}),
                },
            },
            timeout=90,
        )

    def delete_bucket(self, structure, bucket_name):
        """Delete a manual bucket through RGWSquared."""
        return self._post(
            "/s3struct/bucketDelete",
            {"structure": structure, "bucketName": bucket_name},
            timeout=120,
        )

    def upload_csv(self, content_base64):
        """Upload instruments CSV (base64-encoded) for NFFADI."""
        return self._post(
            "/s3structnffadi/csvUpload", {"content": content_base64}, timeout=60
        )
