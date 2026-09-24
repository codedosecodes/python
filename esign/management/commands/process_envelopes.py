from django.core.management.base import BaseCommand

from esign.workflows import expire_due_envelopes


class Command(BaseCommand):
    help = "Expire due envelopes and revoke their outstanding signing sessions."

    def handle(self, *args, **options):
        count = expire_due_envelopes()
        self.stdout.write(self.style.SUCCESS(f"Expired {count} envelope(s)."))
