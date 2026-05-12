"""S3 operations with per-tenant credentials.

Management client (area-mgmt keys): for bucket creation/deletion and file ops.
Area-mgmt keys are fetched dynamically from the RGW Admin REST API
using RGW admin user credentials (S3_ACCESS_KEY/S3_SECRET_KEY with
users=* cap). Cached in Tenant model.
"""

import logging
import hmac
import hashlib
import base64
from email.utils import formatdate

import boto3
import urllib3
import requests
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

# Development RGW may use a private/self-signed CA; production should verify TLS.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def _make_s3_client(access_key, secret_key):
    """Create a boto3 S3 client with the given credentials."""
    return boto3.client(
        "s3",
        endpoint_url=settings.S3_ENDPOINT,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=settings.S3_REGION,
        verify=settings.S3_VERIFY_SSL,
    )


def _rgw_admin_api_get(path, params=""):
    """GET request to Ceph RGW Admin REST API using RGW admin credentials (SigV2)."""
    access_key = settings.S3_ACCESS_KEY
    secret_key = settings.S3_SECRET_KEY
    date = formatdate(timeval=None, localtime=False, usegmt=True)

    # Ceph RGW Admin REST API accepts SigV2 for this deployment.
    string_to_sign = f"GET\n\n\n{date}\n{path}"
    signature = base64.b64encode(
        hmac.new(secret_key.encode(), string_to_sign.encode(), hashlib.sha1).digest()
    ).decode()

    url = f"{settings.S3_ENDPOINT}{path}"
    if params:
        url += f"?{params}"

    resp = requests.get(
        url,
        headers={
            "Date": date,
            "Authorization": f"AWS {access_key}:{signature}",
        },
        verify=settings.S3_VERIFY_SSL,
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def fetch_mgmt_keys(tenant):
    """Fetch {TENANT}$area-mgmt S3 keys from RGW Admin API. Caches in Tenant model."""
    if tenant.mgmt_keys_fresh():
        return tenant.get_mgmt_keys()

    uid = tenant.mgmt_uid  # e.g., "NFFADI$area-mgmt"
    logger.info(f"Fetching S3 keys for {uid} from RGW Admin API")

    data = _rgw_admin_api_get("/admin/user", f"uid={uid}")
    keys = data.get("keys", [])
    if not keys:
        raise RuntimeError(f"No S3 keys found for {uid}")

    key = keys[0]
    tenant.mgmt_access_key = key["access_key"]
    tenant.mgmt_secret_key = key["secret_key"]
    tenant.mgmt_keys_updated_at = timezone.now()
    tenant.save(
        update_fields=["mgmt_access_key", "mgmt_secret_key", "mgmt_keys_updated_at"]
    )

    return tenant.get_mgmt_keys()


def get_mgmt_s3_client(tenant):
    """S3 client using tenant's area-mgmt credentials. For bucket lifecycle ops."""
    access_key, secret_key = fetch_mgmt_keys(tenant)
    return _make_s3_client(access_key, secret_key)


def create_bucket(tenant, bare_name):
    """Create a bucket using tenant's area-mgmt credentials.
    bare_name = the name without tenant prefix (e.g., "nffa-di_cnr-iom.ts_myproject").
    """
    s3 = get_mgmt_s3_client(tenant)
    s3.create_bucket(Bucket=bare_name)
    logger.info(f"Created bucket {bare_name} in tenant {tenant.code}")


def delete_bucket(tenant, bare_name):
    """Delete a bucket and all its objects. Uses area-mgmt credentials."""
    s3 = get_mgmt_s3_client(tenant)

    # RGW refuses bucket deletion until all objects are removed.
    try:
        response = s3.list_objects_v2(Bucket=bare_name)
        if "Contents" in response:
            objects = [{"Key": obj["Key"]} for obj in response["Contents"]]
            s3.delete_objects(Bucket=bare_name, Delete={"Objects": objects})
    except Exception as e:
        logger.warning(f"Could not empty bucket {bare_name} before deletion: {e}")

    s3.delete_bucket(Bucket=bare_name)
    logger.info(f"Deleted bucket {bare_name} from tenant {tenant.code}")


def list_objects(s3_client, bare_name):
    """List all objects in a bucket. Returns list of {key, size, last_modified}."""
    files = []
    try:
        paginator = s3_client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bare_name):
            for obj in page.get("Contents", []):
                files.append(
                    {
                        "key": obj["Key"],
                        "size": obj["Size"],
                        "last_modified": obj["LastModified"],
                    }
                )
    except Exception as e:
        logger.warning(f"Could not list objects in {bare_name}: {e}")
    return files


def upload_object(
    s3_client, bare_name, key, body, content_type="application/octet-stream"
):
    """Upload a file to a bucket."""
    s3_client.put_object(
        Bucket=bare_name,
        Key=key,
        Body=body,
        ContentType=content_type,
    )


def delete_object(s3_client, bare_name, key):
    """Delete a single object from a bucket."""
    s3_client.delete_object(Bucket=bare_name, Key=key)


def download_object(s3_client, bare_name, key):
    """Download an object. Returns (body_bytes, content_type)."""
    response = s3_client.get_object(Bucket=bare_name, Key=key)
    body = response["Body"].read()
    content_type = response.get("ContentType", "application/octet-stream")
    return body, content_type


def get_all_bucket_stats():
    """Fetch storage stats for all buckets from RGW Admin API.

    Returns dict: {bucket_name: {'size_bytes': int, 'num_objects': int}}
    One admin API call keeps dashboards from issuing per-bucket requests.
    """
    try:
        data = _rgw_admin_api_get("/admin/bucket", "stats=true")
    except Exception as e:
        logger.warning(f"Could not fetch bucket stats from RGW: {e}")
        return {}

    stats = {}
    for bucket in data if isinstance(data, list) else [data]:
        name = bucket.get("bucket", "")
        usage = bucket.get("usage", {}).get("rgw.main", {})
        stats[name] = {
            "size_bytes": usage.get("size_actual", 0),
            "num_objects": usage.get("num_objects", 0),
        }
    return stats


def parse_rgwsquared_bucket_name(ms_name, tenant_code):
    """Convert RGWSquared bucket reference to bare S3 name.

    RGWSquared returns "NFFADI:275" or "NFFADI/275" in various contexts.
    The bare name for S3 ops (with tenanted credentials) is just "275".
    """
    for prefix in [f"{tenant_code}:", f"{tenant_code}/"]:
        if ms_name.startswith(prefix):
            return ms_name[len(prefix) :]
    return ms_name
