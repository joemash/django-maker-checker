"""Test actions exercising all three extension styles."""

from django_maker_checker.actions import approval_action
from django_maker_checker.exceptions import ApprovalError
from django_maker_checker.model_change import ModelChangeHandler
from django_maker_checker.registry import ActionHandler, Scope, register
from tests.testapp.models import Document

PUBLISH = "document_publish"
EDIT = "document_edit"
FLAKY = "flaky_action"
MERGE = "document_merge"


@approval_action(
    PUBLISH,
    label="Publish Document",
    category="Documents",
    lock_key=lambda p: f"document:{p['document_id']}",
    lock_keys=lambda p: [f"document:{p['document_id']}"],  # also blocks other actions on the same document
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

    def lock_keys(self, payload):
        return [f"item:{payload['n']}"]

    def execute(self, approval_request, context):
        if type(self).fail:
            raise RuntimeError("downstream unavailable")
        return {"ok": True}


def _merge_condition_errors(conditions):
    minimum = conditions.get("min_documents")
    if minimum is not None and (not isinstance(minimum, int) or minimum < 2):
        return ["The minimum number of documents must be a whole number of at least 2."]
    return []


def _describe_merge_conditions(conditions):
    minimum = conditions.get("min_documents")
    return {"Applies to": f"Merges of {minimum} or more documents" if minimum else "Every merge"}


@approval_action(
    MERGE,
    label="Merge Documents",
    category="Documents",
    lock_key=lambda p: f"merge-into:{p['document_ids'][0]}",  # the first document is the one merged into
    lock_keys=lambda p: [f"document:{pk}" for pk in p["document_ids"]],
    target_label=lambda p: p["title"],
    matches=lambda policy, p: len(p["document_ids"]) >= (policy.conditions or {}).get("min_documents", 0),
    validate_conditions=_merge_condition_errors,
    describe_conditions=_describe_merge_conditions,
)
def merge_documents(*, tenant, maker, context, document_ids, title):
    """Touches several documents at once: every one of them is locked while the request is open."""
    Document.objects.filter(tenant=tenant, pk__in=document_ids).update(title=title)
    return {"merged": sorted(document_ids)}
