from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("storage", "0005_unique_active_ceph_username"),
    ]

    operations = [
        migrations.AlterField(
            model_name="tenant",
            name="mgmt_access_key",
            field=models.CharField(blank=True, max_length=1024),
        ),
        migrations.AlterField(
            model_name="tenant",
            name="mgmt_secret_key",
            field=models.CharField(blank=True, max_length=1024),
        ),
        migrations.AlterField(
            model_name="tenantmembership",
            name="s3_access_key",
            field=models.CharField(blank=True, max_length=1024),
        ),
        migrations.AlterField(
            model_name="tenantmembership",
            name="s3_secret_key",
            field=models.CharField(blank=True, max_length=1024),
        ),
    ]
