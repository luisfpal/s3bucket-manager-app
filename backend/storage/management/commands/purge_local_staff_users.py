"""Remove legacy local-password staff accounts after Authentik admin migration."""

from django.core.management.base import BaseCommand

from storage.models import User


class Command(BaseCommand):
    help = "Delete local idp_source staff users that used Django password auth."

    def handle(self, *args, **options):
        qs = User.objects.filter(idp_source="local", is_staff=True)
        count = qs.count()
        if count:
            qs.delete()
            self.stdout.write(self.style.WARNING(f"Deleted {count} local staff user(s)."))
        else:
            self.stdout.write("No local staff users to purge.")
