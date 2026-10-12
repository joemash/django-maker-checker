"""Settings read by the engine.

``APPROVALS_TENANT_MODEL``  "app_label.Model" of the tenant (required; referenced by migrations)
``APPROVALS_ROLE_MODEL``    "app_label.Model" of a tenant role that can be named as an approver (required)
``APPROVALS_ADAPTER``       dotted path of the ``ApprovalsAdapter`` subclass (required)
``APPROVALS_CACHE_TIMEOUT`` seconds the per-user pending count is cached (default 60)
``APPROVALS_CACHE_ALIAS``   cache used for pending counts (default "default")
``APPROVALS_REFERENCE_PREFIX`` prefix of request references, as in ``REQ-2610-00042`` (default "REQ")
"""

from functools import cache

from django.conf import settings
from django.core.cache import caches
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string


def _required(name):
    value = getattr(settings, name, None)
    if not value:
        raise ImproperlyConfigured(f"django_maker_checker requires the {name} setting")
    return value


def tenant_model_name():
    return _required("APPROVALS_TENANT_MODEL")


def role_model_name():
    return _required("APPROVALS_ROLE_MODEL")


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
    return _adapter_for(_required("APPROVALS_ADAPTER"))
