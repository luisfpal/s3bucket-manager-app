"""Ensure the configured local admin account exists.

Uses DJANGO_SUPERUSER_USERNAME, DJANGO_SUPERUSER_PASSWORD, DJANGO_SUPERUSER_EMAIL
environment variables. Skips only when the local admin is completely unconfigured.
"""

import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from storage.models import User


class Command(BaseCommand):
    help = "Create or repair the configured local staff admin account"

    def handle(self, *args, **options):
        username = os.getenv("DJANGO_SUPERUSER_USERNAME", "").strip()
        password = os.getenv("DJANGO_SUPERUSER_PASSWORD", "")
        email = os.getenv("DJANGO_SUPERUSER_EMAIL", "").strip()

        if not username and not password and not email:
            self.stdout.write("Local admin bootstrap not configured, skipping")
            return

        missing = [
            name
            for name, value in [
                ("DJANGO_SUPERUSER_USERNAME", username),
                ("DJANGO_SUPERUSER_PASSWORD", password),
                ("DJANGO_SUPERUSER_EMAIL", email),
            ]
            if not value
        ]
        if missing:
            message = (
                "Local admin bootstrap is partially configured. Missing: "
                + ", ".join(missing)
            )
            if settings.DEBUG:
                self.stdout.write(self.style.WARNING(f"{message}; skipping"))
                return
            raise CommandError(message)

        user, created = User.objects.get_or_create(
            username=username,
            defaults={
                "email": email,
                "external_id": f"{username}-local",
                "idp_source": "local",
            },
        )

        expected_external_id = f"{username}-local"
        if (
            not created
            and user.external_id
            and user.external_id != expected_external_id
            and user.idp_source != "local"
        ):
            raise CommandError(
                f'Refusing to convert non-local user "{username}" into local admin'
            )

        user.email = email
        if not user.external_id:
            user.external_id = expected_external_id
        user.idp_source = "local"
        user.is_staff = True
        user.is_superuser = True
        user.is_active = True
        user.is_approved = True
        user.set_password(password)
        user.save()

        action = "created" if created else "ensured"
        self.stdout.write(self.style.SUCCESS(f'Local admin "{username}" {action}'))
