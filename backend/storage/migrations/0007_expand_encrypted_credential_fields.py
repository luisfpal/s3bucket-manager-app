from django.db import migrations


def encrypt_cached_credentials(apps, schema_editor):
    from storage.services.crypto import encrypt_if_needed

    Tenant = apps.get_model("storage", "Tenant")
    TenantMembership = apps.get_model("storage", "TenantMembership")

    for tenant in Tenant.objects.all().only("id", "mgmt_access_key", "mgmt_secret_key"):
        updated = False
        if tenant.mgmt_access_key:
            new_value = encrypt_if_needed(tenant.mgmt_access_key)
            if new_value != tenant.mgmt_access_key:
                tenant.mgmt_access_key = new_value
                updated = True
        if tenant.mgmt_secret_key:
            new_value = encrypt_if_needed(tenant.mgmt_secret_key)
            if new_value != tenant.mgmt_secret_key:
                tenant.mgmt_secret_key = new_value
                updated = True
        if updated:
            tenant.save(update_fields=["mgmt_access_key", "mgmt_secret_key"])

    for membership in TenantMembership.objects.all().only(
        "id", "s3_access_key", "s3_secret_key"
    ):
        updated = False
        if membership.s3_access_key:
            new_value = encrypt_if_needed(membership.s3_access_key)
            if new_value != membership.s3_access_key:
                membership.s3_access_key = new_value
                updated = True
        if membership.s3_secret_key:
            new_value = encrypt_if_needed(membership.s3_secret_key)
            if new_value != membership.s3_secret_key:
                membership.s3_secret_key = new_value
                updated = True
        if updated:
            membership.save(update_fields=["s3_access_key", "s3_secret_key"])


class Migration(migrations.Migration):
    dependencies = [
        ("storage", "0006_encrypt_cached_credentials"),
    ]

    operations = [
        migrations.RunPython(encrypt_cached_credentials, migrations.RunPython.noop),
    ]
