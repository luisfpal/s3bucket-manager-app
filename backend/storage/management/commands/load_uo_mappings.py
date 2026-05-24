"""Load UO mapping fixtures from YAML files in storage/fixtures/.

Idempotent: safe to run at startup and after tenant activation.
Silently skips tenants that don't exist yet (e.g., NFFADI not activated).

Usage:
    python manage.py load_uo_mappings               # loads all fixtures
    python manage.py load_uo_mappings --tenant NFFADI  # loads only NFFADI
"""

import os
import yaml
import logging
from django.core.management.base import BaseCommand
from storage.models import Tenant, UOMapping

logger = logging.getLogger(__name__)

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "fixtures")


class Command(BaseCommand):
    help = "Load tenant UO mapping fixtures from YAML files in storage/fixtures/"

    def add_arguments(self, parser):
        parser.add_argument(
            "--tenant",
            type=str,
            default=None,
            help="Only load fixtures for this tenant code (e.g. NFFADI)",
        )

    def handle(self, *args, **options):
        target_tenant = options.get("tenant")
        loaded = 0
        skipped = 0

        for filename in sorted(os.listdir(FIXTURES_DIR)):
            if not filename.endswith(".yaml") and not filename.endswith(".yml"):
                continue

            filepath = os.path.join(FIXTURES_DIR, filename)
            with open(filepath, "r") as f:
                data = yaml.safe_load(f)

            if not isinstance(data, dict) or "tenant" not in data or "uo_mappings" not in data:
                continue

            tenant_code = data["tenant"]
            if target_tenant and tenant_code != target_tenant:
                continue

            tenant = Tenant.objects.filter(code=tenant_code, is_active=True).first()
            if not tenant:
                skipped += 1
                logger.debug(f"load_uo_mappings: tenant {tenant_code} not yet activated, skipping")
                continue

            for entry in data["uo_mappings"]:
                institution_name = entry.get("institution_name", "").strip()
                uo_code = entry.get("uo_code", "").strip()
                if not institution_name or not uo_code:
                    continue
                UOMapping.objects.update_or_create(
                    tenant=tenant,
                    uo_code=uo_code,
                    defaults={"institution_name": institution_name},
                )
                loaded += 1

            self.stdout.write(
                f"Loaded {loaded} UO mappings for {tenant_code} from {filename}"
            )

        if skipped:
            logger.debug(f"load_uo_mappings: skipped {skipped} fixture(s) — tenant not yet activated")
