from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("storage", "0009_alter_bucket_owner_alter_user_external_id_and_more"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="tenant",
            name="mgmt_access_key",
        ),
        migrations.RemoveField(
            model_name="tenant",
            name="mgmt_secret_key",
        ),
        migrations.RemoveField(
            model_name="tenant",
            name="mgmt_keys_updated_at",
        ),
        migrations.RemoveField(
            model_name="tenantmembership",
            name="s3_access_key",
        ),
        migrations.RemoveField(
            model_name="tenantmembership",
            name="s3_secret_key",
        ),
        migrations.RemoveField(
            model_name="tenantmembership",
            name="credentials_updated_at",
        ),
    ]
