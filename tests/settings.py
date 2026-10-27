"""Settings for the library's own tests.

SQLite in memory by default. Set ``DATABASE_URL=postgres://user:password@host:5432/name`` to run against PostgreSQL,
which is what CI does: row locking and partial unique indexes only take full effect there.

``replica`` is a second, separate database that only ``test_read_replicas`` uses, as a read replica that never
catches up.
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


def _database(test_suffix=""):
    url = os.environ.get("DATABASE_URL")
    if not url:
        return {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}
    parts = urlparse(url)
    name = parts.path.lstrip("/")
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": name,
        "USER": parts.username or "",
        "PASSWORD": parts.password or "",
        "HOST": parts.hostname or "",
        "PORT": str(parts.port or ""),
        "TEST": {"NAME": f"test_{name}{test_suffix}"},
    }


DATABASES = {"default": _database(), "replica": _database(test_suffix="_replica")}
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

APPROVALS_TENANT_MODEL = "testapp.Tenant"
APPROVALS_ROLE_MODEL = "testapp.Role"
APPROVALS_ADAPTER = "tests.testapp.adapter.TestAdapter"
