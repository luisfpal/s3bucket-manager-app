from django.db import migrations, models


def forwards(apps, schema_editor):
    Tenant = apps.get_model("storage", "Tenant")
    BucketPermission = apps.get_model("storage", "BucketPermission")
    User = apps.get_model("storage", "User")

    BucketPermission.objects.filter(source="microservice").update(source="rgwsquared")

    Tenant.objects.exclude(code="NFFADI").delete()

    orphan_users = User.objects.filter(memberships__isnull=True)
    protected_orphans = orphan_users.filter(is_staff=True) | orphan_users.filter(
        is_superuser=True
    )
    protected_ids = protected_orphans.values_list("id", flat=True)

    cleanup_orphans = orphan_users.exclude(id__in=protected_ids)

    placeholder_orphans = cleanup_orphans.filter(
        email__iendswith="@placeholder.local", external_id__startswith="ms:"
    )
    placeholder_orphans.delete()

    cleanup_orphans.exclude(
        email__iendswith="@placeholder.local", external_id__startswith="ms:"
    ).update(is_active=False)


def backwards(apps, schema_editor):
    BucketPermission = apps.get_model("storage", "BucketPermission")
    BucketPermission.objects.filter(source="rgwsquared").update(source="microservice")


class Migration(migrations.Migration):
    dependencies = [
        ("storage", "0007_expand_encrypted_credential_fields"),
    ]

    operations = [
        migrations.RenameField(
            model_name="tenant",
            old_name="microservice_structure",
            new_name="rgwsquared_structure",
        ),
        migrations.AlterField(
            model_name="bucketpermission",
            name="source",
            field=models.CharField(
                choices=[("rgwsquared", "From RGWSquared"), ("local", "Local Sharing")],
                default="rgwsquared",
                max_length=15,
            ),
        ),
        migrations.RunPython(forwards, backwards),
    ]
