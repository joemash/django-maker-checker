"""Test actions exercising all three extension styles."""

from django_maker_checker.actions import approval_action
from django_maker_checker.exceptions import ApprovalError
from django_maker_checker.model_change import ModelChangeHandler
from django_maker_checker.registry import ActionHandler, Scope, register
from tests.testapp.models import Document

PUBLISH = "document_publish"
EDIT = "document_edit"
FLAKY = "flaky_action"


@approval_action(
    PUBLISH,
    label="Publish Document",
    category="Documents",
    lock_key=lambda p: f"document:{p['document_id']}",
    target_label=lambda p: Document.objects.get(pk=p["document_id"]).title,
    snapshot_before=lambda tenant, p: {"published": Document.objects.get(pk=p["document_id"]).published},
    scopes=(Scope.GLOBAL, Scope.SCOPED),
    scope_keys=lambda tenant, p: [f"folder:{Document.objects.get(pk=p['document_id']).folder}"],
)
def publish_document(*, tenant, maker, context, document_id, note=""):
    document = Document.objects.get(pk=document_id, tenant=tenant)
    if document.published:
        raise ApprovalError("Already published.")
    document.published = True
    document.save(update_fields=["published"])
    return {"document_id": document.pk, "by": maker.get_username(), "note": note}


@register
class DocumentEditHandler(ModelChangeHandler):
    action_type = EDIT
    label = "Edit Document"
    category = "Documents"
    model = Document
    fields = ("title", "amount")


@register
class FlakyHandler(ActionHandler):
    """Fails until ``FlakyHandler.fail`` is switched off, for execution-failure tests."""

    action_type = FLAKY
    label = "Flaky Action"
    category = "Testing"
    fail = True

    def lock_key(self, payload):
        return f"flaky:{payload['n']}"

    def execute(self, approval_request, context):
        if type(self).fail:
            raise RuntimeError("downstream unavailable")
        return {"ok": True}
