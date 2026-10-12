"""Settings for the single-tenant specification (``tests/single_tenant``): a plain Django project that sets none of
the ``APPROVALS_*`` model or adapter settings, so the built-in tenant, ``auth.Group`` roles and the built-in adapter
are used."""

from tests.settings import CACHES, DATABASES, DEFAULT_AUTO_FIELD, SECRET_KEY, TIME_ZONE, USE_TZ  # noqa: F401

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django_maker_checker",
    "tests.single_tenant",
]
MAILERS = {"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}}  # Django 6.1+; ignored before
