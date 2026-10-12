"""Django app configuration.

The package is ``django_maker_checker`` but the app label is ``approvals``, which keeps table names (``approvals_*``)
and migration history stable for hosts.
"""

from django.apps import AppConfig
from django.db.models.signals import post_migrate
from django.utils.translation import gettext_lazy as _

# Imported for its side effect: it fills in the tenant and role model defaults before any model is loaded.
from django_maker_checker import conf


class ApprovalsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "django_maker_checker"
    label = "approvals"
    verbose_name = _("Approvals (Maker-Checker)")

    def ready(self):
        if conf.adapter_path() == conf.DEFAULT_ADAPTER:
            # The built-in adapter checks Django permissions, so they are created for every registered action.
            from django_maker_checker.auth_adapter import sync_permissions  # imports models, so only after loading

            post_migrate.connect(sync_permissions, sender=self, dispatch_uid="django_maker_checker.sync_permissions")
