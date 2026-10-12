"""Settings read by the engine.

``APPROVALS_TENANT_MODEL``  "app_label.Model" of the tenant (default "approvals.Tenant", the built-in single tenant)
``APPROVALS_ROLE_MODEL``    "app_label.Model" of a role that can be named as an approver (default "auth.Group")
``APPROVALS_ADAPTER``       dotted path of the ``ApprovalsAdapter`` subclass (default: the built-in adapter for Django
                            users, groups and permissions; required with your own tenant model)
``APPROVALS_CACHE_TIMEOUT`` seconds the per-user pending count is cached (default 60)
``APPROVALS_CACHE_ALIAS``   cache used for pending counts (default "default")
``APPROVALS_REFERENCE_PREFIX`` prefix of request references, as in ``REQ-2610-00042`` (default "REQ")

The tenant and role defaults are written onto ``settings`` when this module is first imported (the app config imports
it), because models and migrations read ``settings.APPROVALS_TENANT_MODEL`` / ``APPROVALS_ROLE_MODEL`` directly, as
they do ``AUTH_USER_MODEL``.
"""

from functools import cache

from django.conf import settings
from django.core.cache import caches
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string

DEFAULT_TENANT_MODEL = "approvals.Tenant"
DEFAULT_ROLE_MODEL = "auth.Group"
DEFAULT_ADAPTER = "django_maker_checker.auth_adapter.AuthApprovalsAdapter"


def _apply_model_defaults():
    for name, default in (
        ("APPROVALS_TENANT_MODEL", DEFAULT_TENANT_MODEL),
        ("APPROVALS_ROLE_MODEL", DEFAULT_ROLE_MODEL),
    ):
        if not getattr(settings, name, None):
            setattr(settings, name, default)


_apply_model_defaults()


def tenant_model_name():
    return getattr(settings, "APPROVALS_TENANT_MODEL", None) or DEFAULT_TENANT_MODEL


def role_model_name():
    return getattr(settings, "APPROVALS_ROLE_MODEL", None) or DEFAULT_ROLE_MODEL


def uses_builtin_tenant():
    """True when the project has no tenant model of its own (single-tenant setup)."""
    return tenant_model_name() == DEFAULT_TENANT_MODEL


def adapter_path():
    path = getattr(settings, "APPROVALS_ADAPTER", None)
    if path:
        return path
    if uses_builtin_tenant():
        return DEFAULT_ADAPTER
    raise ImproperlyConfigured(
        "django_maker_checker requires the APPROVALS_ADAPTER setting when APPROVALS_TENANT_MODEL is your own model"
    )


def cache_timeout():
    return getattr(settings, "APPROVALS_CACHE_TIMEOUT", 60)


def counts_cache():
    return caches[getattr(settings, "APPROVALS_CACHE_ALIAS", "default")]


def reference_prefix():
    return getattr(settings, "APPROVALS_REFERENCE_PREFIX", "REQ")


# Adapters are stateless, so one instance per dotted path is reused for the life of the process.
@cache
def _adapter_for(path):
    return import_string(path)()


def get_adapter():
    """The configured adapter instance (one per process)."""
    return _adapter_for(adapter_path())
