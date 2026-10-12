from django.apps import AppConfig


class SingleTenantConfig(AppConfig):
    name = "tests.single_tenant"
    label = "single_tenant"

    def ready(self):
        from tests.single_tenant import actions  # noqa: F401  (registers the action)
