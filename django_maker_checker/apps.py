"""Django app configuration.

The package is ``django_maker_checker`` but the app label is ``approvals``, which keeps table names (``approvals_*``)
and migration history stable for hosts.
"""

from django.apps import AppConfig


class ApprovalsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "django_maker_checker"
    label = "approvals"
    verbose_name = "Approvals (Maker-Checker)"
