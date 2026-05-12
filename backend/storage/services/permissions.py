"""Permission checks for bucket and file operations.

Central place for all authorization logic. Views call these functions
instead of reimplementing permission checks inline.

Permission model:
- Bucket owner (BucketPermission.permission='owner'): full control
- RW user: can view, upload, delete own files
- RO user: can view, download only
- Admin (is_staff): full control over tenant resources

File deletion rule for shared buckets:
- Owner can delete ANY file
- Shared RW user can only delete files THEY uploaded (tracked via FileUploadRecord)
"""

import logging

from storage.models import BucketPermission, FileUploadRecord

logger = logging.getLogger(__name__)


def get_user_permission(user, bucket):
    """Get user's permission level on a bucket. Returns 'owner'/'rw'/'ro'/None."""
    if user.is_staff:
        return "owner"
    try:
        perm = BucketPermission.objects.get(bucket=bucket, user=user)
        return perm.permission
    except BucketPermission.DoesNotExist:
        return None


def can_view_bucket(user, bucket):
    """User can view bucket if they have any permission on it, or are admin."""
    return get_user_permission(user, bucket) is not None


def can_create_bucket(membership):
    """User can create local research buckets if they have RW or admin role."""
    return membership.is_active and membership.role in ("rw", "admin")


def can_delete_bucket(user, bucket):
    """Only bucket owner (or admin) can delete, and only if is_deletable=True."""
    if not bucket.is_deletable:
        return False
    perm = get_user_permission(user, bucket)
    return perm in ("owner",)


def can_upload_file(user, bucket):
    """RW or owner permission required to upload."""
    perm = get_user_permission(user, bucket)
    return perm in ("rw", "owner")


def can_delete_file(user, bucket, file_key):
    """Owner can delete any file. Shared RW can only delete their own uploads."""
    perm = get_user_permission(user, bucket)
    if perm == "owner":
        return True
    if perm == "rw":
        return FileUploadRecord.objects.filter(
            bucket=bucket, file_key=file_key, uploaded_by=user
        ).exists()
    return False


def can_download_file(user, bucket):
    """Any permission (ro/rw/owner) allows download."""
    return can_view_bucket(user, bucket)
