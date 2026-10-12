"""The host contract: what the engine needs from a host project, and what it promises not to depend on.

A host provides three settings (``APPROVALS_TENANT_MODEL``, ``APPROVALS_ROLE_MODEL``, ``APPROVALS_ADAPTER``) and an
``ApprovalsAdapter`` subclass answering membership, permission, notification and audit questions
(``tests/testapp/adapter.py`` is a complete, minimal one). Optional settings tune the cache and request references.

In return the engine stays headless: it imports nothing beyond Django, django-fsm and the standard library, ships no
templates, static files or URLs, and needs Celery only for its optional task module. The SLA pass runs from Celery
beat or from ``manage.py process_approval_slas``.
"""

import ast
import pathlib
from io import StringIO

from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.test import SimpleTestCase, override_settings

from django_maker_checker import conf
from tests.base import EngineTestCase
from tests.testapp.handlers import PUBLISH

PACKAGE = pathlib.Path(__file__).resolve().parent.parent / "django_maker_checker"
ALLOWED_TOP_LEVEL = {
    "__future__",
    "abc",
    "django_maker_checker",
    "dataclasses",
    "datetime",
    "decimal",
    "django",
    "django_fsm",
    "functools",
    "json",
    "logging",
    "typing",
}
OPTIONAL_IMPORTS = {"celery": {"tasks.py"}}  # extra -> the only modules allowed to import it


def _imported_names(path):
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            yield node.module


class Settings(SimpleTestCase):
    def test_a_missing_adapter_setting_is_reported_as_misconfiguration(self):
        with override_settings(APPROVALS_ADAPTER=None), self.assertRaises(ImproperlyConfigured):
            conf.get_adapter()

    def test_the_pending_count_cache_timeout_is_configurable(self):
        with override_settings(APPROVALS_CACHE_TIMEOUT=5):
            self.assertEqual(conf.cache_timeout(), 5)

    def test_pending_counts_can_use_a_dedicated_cache(self):
        caches = {
            "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
            "approvals": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"},
        }
        with override_settings(CACHES=caches, APPROVALS_CACHE_ALIAS="approvals"):
            self.assertEqual(type(conf.counts_cache()).__name__, "DummyCache")


class OptionalSettings(EngineTestCase):
    def test_request_references_use_a_configurable_prefix(self):
        self.policy(PUBLISH, [self.role_reviewer])
        with override_settings(APPROVALS_REFERENCE_PREFIX="CHG"):
            self.assertRegex(self.raise_request().reference, r"^CHG-\d{4}-\d{5}$")


class SlaCommand(EngineTestCase):
    def test_the_sla_pass_can_run_from_cron_without_celery(self):
        out = StringIO()
        call_command("process_approval_slas", stdout=out)
        self.assertEqual(out.getvalue().strip(), "Warned 0, escalated 0.")


class EngineBoundaries(SimpleTestCase):
    databases = {"default"}  # makemigrations --check reads the migration history table

    def test_engine_imports_only_django_fsm_and_stdlib(self):
        offenders = []
        for path in PACKAGE.rglob("*.py"):
            module = str(path.relative_to(PACKAGE))
            for name in _imported_names(path):
                top = name.split(".")[0]
                allowed = top in ALLOWED_TOP_LEVEL or module in OPTIONAL_IMPORTS.get(top, ())
                if not allowed:
                    offenders.append(f"{module}: {name}")
        self.assertEqual(offenders, [])

    def test_engine_ships_no_templates_static_or_urls(self):
        self.assertFalse((PACKAGE / "templates").exists())
        self.assertFalse((PACKAGE / "static").exists())
        self.assertFalse((PACKAGE / "urls.py").exists())

    def test_migrations_are_in_sync_with_models(self):
        out = StringIO()
        call_command("makemigrations", "approvals", "--check", "--dry-run", stdout=out)
        self.assertIn("No changes detected", out.getvalue())
