"""RGWSquared service client.

All RGWSquared calls go through this one file. If the RGWSquared API changes
or we switch to IAM Accounts, only this file changes.
"""

import logging
import time
import requests

logger = logging.getLogger(__name__)


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
        resp.raise_for_status()

        # RGWSquared may return plain-text error bodies.
        try:
            data = resp.json()
        except requests.exceptions.JSONDecodeError:
            raise RuntimeError(
                f"RGWSquared {path} returned non-JSON: {resp.text[:200]}"
            )

        return data

    def list_structures(self):
        """Returns list of structure names, e.g. ["NFFADI"]."""
        data = self._post("/s3struct/list")
        return data.get("res", [])

    def list_users(self, structure):
        """Returns list of usernames in a structure."""
        data = self._post("/s3struct/userList", {"structure": structure})
        return data.get("res", [])

    def get_user_info(self, structure, user):
        """Returns {uid, access_key, secret_key, ROBuckets, RWBuckets}.

        ROBuckets/RWBuckets contain strings like "NFFADI:275".
        Strip the "NFFADI:" prefix to get the bare bucket name for S3 ops.
        """
        data = self._post("/s3struct/userInfo", {"structure": structure, "user": user})
        return data.get("res", {})

    def list_buckets(self, structure):
        """Returns list of bucket names (may include non-tenant buckets)."""
        data = self._post("/s3struct/bucketList", {"structure": structure})
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

    def upload_csv(self, content_base64):
        """Upload instruments CSV (base64-encoded) for NFFADI."""
        return self._post(
            "/s3structnffadi/extCSVUpload", {"content": content_base64}, timeout=60
        )

    def sync_proposals(self):
        """Fetch researchers from NFFA-DI proposals API."""
        return self._post("/s3structnffadi/extEPSync", timeout=60)

    def sync_structure(self, tenant_code):
        """Generate structure definition in CouchDB.

        Routes to tenant-specific endpoint: /s3struct{code}/sync
        """
        path = f"/s3struct{tenant_code.lower()}/sync"
        return self._post(path, timeout=60)

    def apply_to_ceph(self, structure):
        """Apply structure to Ceph RGW. May return 504 but completes server-side.

        Returns the response data on success, or raises on non-504 errors.
        On 504: returns None (caller should poll for completion).
        """
        try:
            return self._post("/s3struct/sync", {"structure": structure}, timeout=45)
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 504:
                logger.warning(
                    f"apply_to_ceph({structure}) got 504 — sync continues server-side"
                )
                return None
            raise
        except requests.exceptions.ReadTimeout:
            logger.warning(
                f"apply_to_ceph({structure}) timed out — sync continues server-side"
            )
            return None
