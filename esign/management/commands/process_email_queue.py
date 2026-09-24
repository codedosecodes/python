from django.core.management.base import BaseCommand

from esign.email_service import retry_due_emails


class Command(BaseCommand):
    help = "Deliver queued email and retry due failures with bounded backoff."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)

    def handle(self, *args, **options):
        sent, failed = retry_due_emails(limit=max(1, min(options["limit"], 1000)))
        self.stdout.write(self.style.SUCCESS(f"Sent {sent}; failed {failed}."))
