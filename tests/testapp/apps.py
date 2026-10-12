from django.apps import AppConfig


class TestappConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tests.testapp"
    label = "testapp"

    def ready(self):
        from tests.testapp import handlers  # noqa: F401 - registers the test actions (needs models loaded)
