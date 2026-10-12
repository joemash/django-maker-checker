"""Celery task for the SLA pass (needs the ``celery`` extra: ``pip install django-maker-checker[celery]``).

The task name is fixed (``approvals.tasks.process_approval_slas``) so beat schedules stored in a host's database keep
working however the package is imported. Without Celery, run ``manage.py process_approval_slas`` from cron instead.
"""

import logging

from celery import shared_task

from django_maker_checker.services import process_sla

logger = logging.getLogger(__name__)


@shared_task(name="approvals.tasks.process_approval_slas")
def process_approval_slas():
    """Run periodically (e.g. every 5 minutes): SLA reminders and escalation of overdue approval stages."""
    summary = process_sla()
    if summary["warned"] or summary["escalated"]:
        logger.info("Approval SLA run: %s", summary)
    return summary
