"""Core storage models for users, tenants, buckets, permissions, and mappings."""

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone

from storage.services.crypto import encrypt_if_needed, decrypt_if_needed


class User(AbstractUser):
    """User model with federated identity attributes."""

    external_id = models.CharField(
        max_length=255,
        unique=True,
        db_index=True,
        help_text="Unique identifier from the identity provider (OAuth2 'sub').",
    )

    idp_source = models.CharField(
        max_length=100,
        default="authentik",
        db_index=True,
        help_text="Identity provider that authenticated this user.",
    )

    institution = models.CharField(
        max_length=255,
        blank=True,
        db_index=True,
        help_text="Institution affiliation from OAuth2 claims.",
    )

    department = models.CharField(
        max_length=255,
        blank=True,
        help_text="Department within institution (e.g., 'Physics', 'Biology')",
    )

    affiliation_status = models.CharField(
        max_length=50,
        blank=True,
        choices=[
            ("faculty", "Faculty"),
            ("staff", "Staff"),
            ("student", "Student"),
            ("affiliate", "Affiliate"),
            ("guest", "Guest"),
        ],
        help_text="User's role/status at institution",
    )

    orcid = models.CharField(
        max_length=19,  # Format: 0000-0002-1825-0097
        blank=True,
        unique=True,
        null=True,
        help_text="ORCID identifier for researchers",
    )

    profile_picture_url = models.URLField(
        blank=True, help_text="URL to user's profile picture from IdP"
    )

    last_idp_sync = models.DateTimeField(
        auto_now=True, help_text="Last time user profile was synced from IdP"
    )

    is_approved = models.BooleanField(
        default=True, help_text="Whether user is approved to access the system"
    )

    notes = models.TextField(blank=True, help_text="Admin notes about this user")

    display_username = models.CharField(
        max_length=150,
        blank=True,
        null=True,
        unique=True,
        help_text=(
            "Collision-safe display name derived from email prefix at first login. "
            "Used for UI display, sharing, and local bucket naming. "
            "Collisions resolved with -2, -3, etc. suffix."
        ),
    )

    email = models.EmailField(
        unique=True, help_text="Email address from IdP (OAuth2 'email' claim)"
    )

    class Meta:
        db_table = "users"
        verbose_name = "User"
        verbose_name_plural = "Users"
        indexes = [
            models.Index(fields=["external_id", "idp_source"]),
            models.Index(fields=["institution"]),
            models.Index(fields=["email"]),
        ]

    def __str__(self):
        """String representation shows name and institution"""
        if self.institution:
            return f"{self.get_full_name()} ({self.institution})"
        return self.get_full_name() or self.username

    def get_full_name(self):
        """Override to provide fallback"""
        full_name = super().get_full_name()
        if not full_name:
            return self.username
        return full_name

    @property
    def display_name(self):
        """Stable display name: display_username if set, else email prefix, else username."""
        if self.display_username:
            return self.display_username
        if self.email and "@" in self.email:
            return self.email.split("@")[0]
        return self.username

    @property
    def is_federated(self):
        """Whether this account came from a federated identity provider."""
        return bool(self.external_id)

    @property
    def can_access_system(self):
        """Single account gate used by API permission checks."""
        return self.is_active and self.is_approved

    def get_identity_summary(self):
        """Identity fields safe to include in audit/debug logs."""
        return {
            "external_id": self.external_id,
            "idp_source": self.idp_source,
            "institution": self.institution,
            "department": self.department,
            "affiliation_status": self.affiliation_status,
            "email": self.email,
        }


class Tenant(models.Model):
    """A research project or laboratory that gets its own S3 namespace.

    Each tenant maps to:
    - A "structure" in RGWSquared (e.g., "NFFADI")
    - Two Ceph RGW users: {code}$area-mgmt (bucket owner) and {code}$ext-users (subusers)
    - A bucket naming prefix for local research buckets
    """

    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=200)
    rgwsquared_structure = models.CharField(
        max_length=50,
        blank=True,
        help_text="Structure name for RGWSquared API calls (e.g., 'NFFADI')",
    )
    bucket_name_prefix = models.CharField(
        max_length=50,
        blank=True,
        help_text="Prefix for local research bucket names (e.g., 'nffa-di')",
    )
    is_active = models.BooleanField(default=True)

    # Cached area-mgmt keys are encrypted on save; callers must use get_mgmt_keys().
    mgmt_access_key = models.CharField(max_length=1024, blank=True)
    mgmt_secret_key = models.CharField(max_length=1024, blank=True)
    mgmt_keys_updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "tenants"
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} ({self.name})"

    def save(self, *args, **kwargs):
        self.mgmt_access_key = encrypt_if_needed(self.mgmt_access_key)
        self.mgmt_secret_key = encrypt_if_needed(self.mgmt_secret_key)
        super().save(*args, **kwargs)

    @property
    def mgmt_uid(self):
        """RGW user ID for this tenant's management account."""
        return f"{self.code}$area-mgmt"

    def get_mgmt_keys(self):
        return (
            decrypt_if_needed(self.mgmt_access_key),
            decrypt_if_needed(self.mgmt_secret_key),
        )

    def mgmt_keys_fresh(self, max_age_seconds=3600):
        """Whether cached management keys can be reused."""
        if not self.mgmt_access_key or not self.mgmt_keys_updated_at:
            return False
        age = (timezone.now() - self.mgmt_keys_updated_at).total_seconds()
        return age < max_age_seconds


class TenantMembership(models.Model):
    """Links a user to a tenant with role and cached S3 credentials.

    ceph_username = preferred_username from Authentik JWT.
    This is the subuser name in Ceph: {TENANT}$ext-users:{ceph_username}
    """

    ROLE_CHOICES = [
        ("ro", "Read-Only"),
        ("rw", "Read-Write"),
        ("admin", "Admin"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="memberships")
    tenant = models.ForeignKey(
        Tenant, on_delete=models.CASCADE, related_name="memberships"
    )
    ceph_username = models.CharField(max_length=200)
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default="ro")
    uo_code = models.CharField(
        max_length=50,
        blank=True,
        help_text="Operational unit code for NFFADI bucket naming (e.g., 'cnr-iom.ts')",
    )
    is_active = models.BooleanField(default=True)

    # RGWSquared userInfo credentials are encrypted on save.
    s3_access_key = models.CharField(max_length=1024, blank=True)
    s3_secret_key = models.CharField(max_length=1024, blank=True)
    credentials_updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "tenant_memberships"
        unique_together = [("user", "tenant")]
        indexes = [
            models.Index(fields=["tenant", "ceph_username"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "ceph_username"],
                condition=models.Q(is_active=True),
                name="unique_active_ceph_username_per_tenant",
            ),
        ]

    def __str__(self):
        return f"{self.ceph_username} @ {self.tenant.code} ({self.role})"

    def save(self, *args, **kwargs):
        self.s3_access_key = encrypt_if_needed(self.s3_access_key)
        self.s3_secret_key = encrypt_if_needed(self.s3_secret_key)
        super().save(*args, **kwargs)

    @property
    def ceph_subuser_uid(self):
        """Full Ceph subuser UID: {TENANT}$ext-users:{ceph_username}"""
        return f"{self.tenant.code}$ext-users:{self.ceph_username}"

    def get_s3_credentials(self):
        return (
            decrypt_if_needed(self.s3_access_key),
            decrypt_if_needed(self.s3_secret_key),
        )

    def credentials_fresh(self, max_age_seconds=300):
        """Check if cached S3 credentials are still fresh (5 min default)."""
        if not self.s3_access_key or not self.credentials_updated_at:
            return False
        age = (timezone.now() - self.credentials_updated_at).total_seconds()
        return age < max_age_seconds


class Bucket(models.Model):
    """S3 bucket metadata.

    Two types:
    - proposal: created by RGWSquared sync, undeletable, named by instrument ID
    - local: created by users for their own research, deletable by owner

    name = bare S3 name as seen by tenanted users (e.g., "275", not "NFFADI/275").
    display_name = human-readable with tenant prefix (e.g., "NFFADI/275").
    """

    PROPOSAL = "proposal"
    LOCAL = "local"
    TYPE_CHOICES = [(PROPOSAL, "Proposal"), (LOCAL, "Local Research")]

    name = models.CharField(
        max_length=255, help_text="Bare S3 bucket name (no tenant prefix)"
    )
    display_name = models.CharField(
        max_length=300, blank=True, help_text="Human-readable name with prefix"
    )
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="buckets")
    owner = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="owned_buckets",
        help_text="Creator. Null for proposal buckets synced from RGWSquared.",
    )
    bucket_type = models.CharField(max_length=10, choices=TYPE_CHOICES)
    is_deletable = models.BooleanField(default=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "buckets"
        unique_together = [("name", "tenant")]
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["tenant", "bucket_type"]),
        ]

    def __str__(self):
        return self.display_name or f"{self.tenant.code}/{self.name}"

    def save(self, *args, **kwargs):
        if not self.display_name:
            self.display_name = self.name
        if self.bucket_type == self.PROPOSAL:
            self.is_deletable = False
        super().save(*args, **kwargs)


class BucketPermission(models.Model):
    """Per-user permission on a bucket.

    source='rgwsquared': synced from RGWSquared userInfo (ROBuckets/RWBuckets)
    source='local': granted by bucket owner for shared local research buckets
    """

    PERMISSION_CHOICES = [
        ("ro", "Read-Only"),
        ("rw", "Read-Write"),
        ("owner", "Owner"),
    ]
    SOURCE_CHOICES = [
        ("rgwsquared", "From RGWSquared"),
        ("local", "Local Sharing"),
    ]

    bucket = models.ForeignKey(
        Bucket, on_delete=models.CASCADE, related_name="permissions"
    )
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="bucket_permissions"
    )
    permission = models.CharField(max_length=10, choices=PERMISSION_CHOICES)
    source = models.CharField(
        max_length=15, choices=SOURCE_CHOICES, default="rgwsquared"
    )
    granted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "bucket_permissions"
        unique_together = [("bucket", "user")]

    def __str__(self):
        return f"{self.user.username} → {self.bucket} ({self.permission})"


class FileUploadRecord(models.Model):
    """Tracks who uploaded a file. Needed for shared buckets where
    shared RW users can only delete their own files."""

    bucket = models.ForeignKey(
        Bucket, on_delete=models.CASCADE, related_name="upload_records"
    )
    file_key = models.CharField(max_length=1024)
    uploaded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "file_upload_records"
        unique_together = [("bucket", "file_key")]

    def __str__(self):
        return f"{self.file_key} by {self.uploaded_by}"


class UOMapping(models.Model):
    """Maps institution name to operational unit code for bucket naming.

    NFFADI local research buckets: nffa-di_{uo_code}_{project_id}
    Example: "CNR - Istituto Officina dei Materiali - Trieste" → "cnr-iom.ts"
    """

    tenant = models.ForeignKey(
        Tenant, on_delete=models.CASCADE, related_name="uo_mappings"
    )
    institution_name = models.CharField(max_length=300)
    uo_code = models.CharField(max_length=50)

    class Meta:
        db_table = "uo_mappings"
        unique_together = [("tenant", "uo_code")]

    def __str__(self):
        return f"{self.uo_code} ({self.institution_name[:50]})"


class GroupTenantMapping(models.Model):
    """Maps an Authentik group name to a tenant.

    Used for @areasciencepark.it users who may belong to multiple tenants.
    The JWT 'groups' claim is matched against this table to determine
    which tenants a user can access.

    Example: "lame-users" → LAME tenant, "nffa-di-users" → NFFADI tenant
    """

    authentik_group = models.CharField(max_length=200, unique=True)
    tenant = models.ForeignKey(
        Tenant, on_delete=models.CASCADE, related_name="group_mappings"
    )

    class Meta:
        db_table = "group_tenant_mappings"

    def __str__(self):
        return f"{self.authentik_group} → {self.tenant.code}"
