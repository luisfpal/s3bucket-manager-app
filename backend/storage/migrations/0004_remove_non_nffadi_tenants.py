"""Remove non-NFFADI tenants (LADE, LAME, LAGE, PRP).

These were placeholder tenants with no real users, buckets, or data.
Django CASCADE handles all FK dependents: TenantMembership, Bucket,
BucketPermission, FileUploadRecord, UOMapping, GroupTenantMapping.
"""

from django.db import migrations


DEAD_TENANTS = [
    # (code, name, bucket_name_prefix)
    ("LADE", "LADE Laboratory", "lade"),
    ("LAME", "LAME Laboratory", "lame"),
    ("LAGE", "LAGE Laboratory", "lage"),
    ("PRP", "PRP", "prp"),
]

DEAD_GROUP_MAPPINGS = [
    # (authentik_group, tenant_code)
    ("lade-users", "LADE"),
    ("lame-users", "LAME"),
    ("lage-users", "LAGE"),
    ("prp-users", "PRP"),
]


def remove_tenants(apps, schema_editor):
    Tenant = apps.get_model("storage", "Tenant")
    codes = [t[0] for t in DEAD_TENANTS]
    deleted, breakdown = Tenant.objects.filter(code__in=codes).delete()
    if deleted:
        print(f"\n  Removed {deleted} objects: {breakdown}")


def restore_tenants(apps, schema_editor):
    Tenant = apps.get_model("storage", "Tenant")
    GroupTenantMapping = apps.get_model("storage", "GroupTenantMapping")

    tenants = {}
    for code, name, prefix in DEAD_TENANTS:
        t, _ = Tenant.objects.get_or_create(
            code=code,
            defaults={
                "name": name,
                "microservice_structure": "",
                "bucket_name_prefix": prefix,
            },
        )
        tenants[code] = t

    for group, tenant_code in DEAD_GROUP_MAPPINGS:
        GroupTenantMapping.objects.get_or_create(
            authentik_group=group,
            defaults={"tenant": tenants[tenant_code]},
        )


class Migration(migrations.Migration):
    dependencies = [
        ("storage", "0003_user_display_username"),
    ]

    operations = [
        migrations.RunPython(remove_tenants, restore_tenants),
    ]
