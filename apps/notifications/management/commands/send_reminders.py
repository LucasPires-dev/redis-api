from django.core.management.base import BaseCommand, CommandError

from apps.notifications import reminders
from apps.notifications.tasks import run

BATCHES = {"hourly": reminders.HOURLY, "daily": reminders.DAILY, "weekly": reminders.WEEKLY}


class Command(BaseCommand):
    help = "Sends the scheduled reminders now (the same ones Celery Beat sends). Never sends one twice."

    def add_arguments(self, parser):
        parser.add_argument("batch", nargs="*", help="hourly, daily and/or weekly (default: all)")

    def handle(self, *args, batch, **options):
        unknown = set(batch) - set(BATCHES)
        if unknown:
            raise CommandError(f"Unknown batch: {', '.join(sorted(unknown))}. Use hourly, daily or weekly.")
        for name in batch or BATCHES:
            self.stdout.write(f"{name}: {run(BATCHES[name])}")
