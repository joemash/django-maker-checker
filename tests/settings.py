"""Settings for the library's own tests.

SQLite in memory by default. Set ``DATABASE_URL=postgres://user:password@host:5432/name`` to run against PostgreSQL,
which is what CI does: row locking and partial unique indexes only take full effect there.
"""

import os
from urllib.parse import urlparse

SECRET_KEY = "tests-only"
USE_TZ = True
TIME_ZONE = "UTC"
INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "tests.testapp",
    "django_maker_checker",
]


def _database():
    url = os.environ.get("DATABASE_URL")
    if not url:
        return {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}
    parts = urlparse(url)
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": parts.path.lstrip("/"),
        "USER": parts.username or "",
        "PASSWORD": parts.password or "",
        "HOST": parts.hostname or "",
        "PORT": str(parts.port or ""),
    }


DATABASES = {"default": _database()}
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

APPROVALS_TENANT_MODEL = "testapp.Tenant"
APPROVALS_ROLE_MODEL = "testapp.Role"
APPROVALS_ADAPTER = "tests.testapp.adapter.TestAdapter"
