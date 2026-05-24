"""Management command: sync active NFFADI tenant with RGWSquared."""

import logging
from django.core.management.base import BaseCommand
from storage.models import Tenant
from storage.services.sync_service import refresh_local_cache

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Sync active NFFADI tenant with RGWSquared"

    def handle(self, *args, **options):
        tenants = Tenant.objects.filter(is_active=True, code="NFFADI").exclude(
            rgwsquared_structure=""
        )
        if not tenants.exists():
            self.stdout.write("No active NFFADI tenant with RGWSquared configured.")
            return

        for tenant in tenants:
            self.stdout.write(f"Syncing {tenant.code}...")
            try:
                stats = refresh_local_cache(tenant)
                self.stdout.write(self.style.SUCCESS(f"  {tenant.code}: {stats}"))
            except Exception as e:
                self.stderr.write(self.style.ERROR(f"  {tenant.code} failed: {e}"))
                logger.exception(f"Background sync failed for {tenant.code}")

        self.stdout.write(self.style.SUCCESS("Sync complete."))
