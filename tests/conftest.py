"""The suite runs twice, once per test project (Django settings can't change within one process):

- ``tests.settings``: a multi-tenant host with its own tenant and role models and adapter (``tests/testapp``);
- ``tests.settings_single``: a plain single-tenant project on the built-in defaults (``tests/single_tenant``).

Each run collects only the specification written for its project.
"""

import os
from pathlib import Path

SINGLE_TENANT_DIR = Path(__file__).parent / "single_tenant"


def pytest_ignore_collect(collection_path, config):
    single_tenant_run = os.environ.get("DJANGO_SETTINGS_MODULE") == "tests.settings_single"
    in_single_tenant_dir = SINGLE_TENANT_DIR in collection_path.parents or collection_path == SINGLE_TENANT_DIR
    if collection_path.is_file() and collection_path.suffix == ".py" and collection_path.name.startswith("test_"):
        return in_single_tenant_dir != single_tenant_run
    return None
