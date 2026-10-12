"""Run the SLA pass once: reminders and escalation of overdue approval steps.

For projects without Celery, schedule it with cron or any job runner, e.g. every 5 minutes:

    */5 * * * *  python manage.py process_approval_slas

It is safe to run concurrently and repeatedly (see ``services.process_sla``).
"""

from django.core.management.base import BaseCommand

from django_maker_checker.services import process_sla


class Command(BaseCommand):
    help = "Send SLA reminders and escalate overdue approval steps."

    def handle(self, *args, **options):
        summary = process_sla()
        self.stdout.write(f"Warned {summary['warned']}, escalated {summary['escalated']}.")
