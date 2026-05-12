"""Serializers for multi-tenant S3 Bucket Manager."""

from rest_framework import serializers
from .models import (
    User,
    Bucket,
    Tenant,
    BucketPermission,
    UOMapping,
    GroupTenantMapping,
)


class UserSerializer(serializers.ModelSerializer):
    display_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "display_name",
            "email",
            "first_name",
            "last_name",
            "external_id",
            "idp_source",
            "institution",
            "department",
            "affiliation_status",
            "orcid",
            "profile_picture_url",
            "is_approved",
            "is_staff",
            "date_joined",
        ]
        read_only_fields = fields

    def get_display_name(self, obj):
        return obj.display_name


class TenantSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tenant
        fields = [
            "id",
            "code",
            "name",
            "rgwsquared_structure",
            "bucket_name_prefix",
            "is_active",
        ]
        read_only_fields = fields


class BucketSerializer(serializers.ModelSerializer):
    owner_name = serializers.SerializerMethodField()
    permission = serializers.SerializerMethodField()
    shared_with_count = serializers.SerializerMethodField()
    size_bytes = serializers.SerializerMethodField()
    num_objects = serializers.SerializerMethodField()

    class Meta:
        model = Bucket
        fields = [
            "id",
            "name",
            "display_name",
            "description",
            "bucket_type",
            "is_deletable",
            "owner",
            "owner_name",
            "permission",
            "shared_with_count",
            "size_bytes",
            "num_objects",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_owner_name(self, obj):
        if obj.owner:
            return obj.owner.display_name
        return obj.tenant.code if obj.tenant else None

    def get_permission(self, obj):
        user = self.context.get("user")
        if not user:
            return None
        from storage.services.permissions import get_user_permission

        return get_user_permission(user, obj)

    def get_shared_with_count(self, obj):
        return (
            BucketPermission.objects.filter(
                bucket=obj,
            )
            .exclude(permission="owner")
            .count()
        )

    def get_size_bytes(self, obj):
        stats = self.context.get("bucket_stats", {})
        return stats.get(obj.name, {}).get("size_bytes", 0)

    def get_num_objects(self, obj):
        stats = self.context.get("bucket_stats", {})
        return stats.get(obj.name, {}).get("num_objects", 0)


class BucketCreateSerializer(serializers.Serializer):
    """Input: project_id (becomes part of the bare bucket name).

    Full name validation is handled by PROJECT_ID_RE in BucketViewSet.create().
    """

    name = serializers.CharField(max_length=63, min_length=2)
    description = serializers.CharField(required=False, default="", allow_blank=True)

    def validate_name(self, value):
        return value.lower()


class FileSerializer(serializers.Serializer):
    key = serializers.CharField()
    size = serializers.IntegerField()
    last_modified = serializers.DateTimeField()
    uploaded_by = serializers.CharField(required=False, allow_null=True)


class FileUploadSerializer(serializers.Serializer):
    file = serializers.FileField()
    key = serializers.CharField(required=False)

    def validate_file(self, value):
        max_size = 100 * 1024 * 1024  # 100MB
        if value.size > max_size:
            raise serializers.ValidationError(
                f"File too large. Maximum size is 100MB. "
                f"Your file is {value.size / (1024 * 1024):.2f}MB"
            )
        return value


class UOMappingSerializer(serializers.ModelSerializer):
    tenant_code = serializers.CharField(source="tenant.code", read_only=True)

    class Meta:
        model = UOMapping
        fields = ["id", "tenant", "tenant_code", "institution_name", "uo_code"]


class GroupTenantMappingSerializer(serializers.ModelSerializer):
    tenant_code = serializers.CharField(source="tenant.code", read_only=True)

    class Meta:
        model = GroupTenantMapping
        fields = ["id", "authentik_group", "tenant", "tenant_code"]
