"""``ModelChangeHandler``: a ready-made handler for "change these fields on one record".

This is the Template Method pattern. The base class owns the whole flow (lock per record, snapshot of the current
values, optimistic check that nobody changed them since submission, apply, Current/Proposed review rows); a subclass
only declares the model and the fields that may change (and, if needed, how to find a tenant's records).

.. code-block:: python

    @register
    class CreditLimitChangeHandler(ModelChangeHandler):
        action_type = "credit_limit_change"
        label = "Credit Limit Change"
        category = "Accounts"
        model = Account
        fields = ("credit_limit",)
        tenant_field = "organisation"

    outcome = CreditLimitChangeHandler.submit_change(account, {"credit_limit": "5000.00"}, maker=request.user,
                                                     justification="Annual review", request=request)
"""

from django.core.exceptions import ValidationError

from django_maker_checker import services
from django_maker_checker.actions import Outcome, to_payload
from django_maker_checker.exceptions import ApprovalError
from django_maker_checker.registry import ActionHandler, ExecutionContext, diff_rows, get_handler


class ModelChangeHandler(ActionHandler):
    """Handler for "change these fields on one record". Subclasses set ``model``, ``fields`` and, if the foreign key
    to the tenant isn't called ``tenant``, ``tenant_field``.

    Payload shape: ``{"pk": …, "label": …, "changes": {field: JSON value}}``.
    """

    model = None
    fields: tuple = ()
    tenant_field = "tenant"  # the model's foreign key to APPROVALS_TENANT_MODEL

    # --- what a subclass may override ---------------------------------------------------------------

    def queryset(self, tenant):
        """Records of ``model`` that belong to ``tenant``. Must be tenant-scoped."""
        return self.model._default_manager.filter(**{self.tenant_field: tenant})

    def tenant_of(self, obj):
        """The tenant that owns ``obj``; that tenant's approval rules apply."""
        return getattr(obj, self.tenant_field)

    def describe(self, obj) -> str:
        return str(obj)

    def apply(self, obj, changes, context: ExecutionContext):
        """Write the approved values. Override to call a service instead of ``save()``."""
        for name, value in changes.items():
            setattr(obj, name, value)
        obj.full_clean(exclude=[f.name for f in obj._meta.fields if f.name not in changes])
        obj.save(update_fields=list(changes))

    # --- the fixed flow --------------------------------------------------------------------------------

    @classmethod
    def submit_change(cls, obj, changes: dict, *, maker, justification="", request=None):
        """Route a change to ``obj`` through maker-checker (applies at once when no policy applies)."""
        handler = get_handler(cls.action_type)
        unknown = set(changes) - set(handler.fields)
        if unknown:
            raise ValueError(f"{cls.__name__} cannot change: {', '.join(sorted(unknown))}")
        payload = {"pk": obj.pk, "label": handler.describe(obj), "changes": to_payload(changes)}
        approval_request, result = services.submit_or_execute(
            cls.action_type,
            tenant=handler.tenant_of(obj),
            maker=maker,
            payload=payload,
            justification=justification,
            request=request,
        )
        return Outcome(approval_request, result)

    def lock_key(self, payload):
        return f"{self.model._meta.label_lower}:{payload['pk']}"

    def target_label(self, payload):
        return payload.get("label", "")

    def _record(self, tenant, pk):
        # Always looked up through the tenant-scoped queryset, so a payload can't reach another tenant's record.
        return self.queryset(tenant).filter(pk=pk).first()

    def _current(self, obj, names):
        # Current values in payload (JSON) form, so they compare equal to the stored snapshot.
        return to_payload({name: getattr(obj, name) for name in names})

    def snapshot_before(self, tenant, payload):
        obj = self._record(tenant, payload["pk"])
        return self._current(obj, payload["changes"]) if obj else {}

    def validate(self, approval_request):
        """Optimistic concurrency check: the fields being changed must still hold the values captured at submission.
        Otherwise approving would silently overwrite someone else's edit, so the request fails instead."""
        payload = approval_request.payload
        obj = self._record(approval_request.tenant, payload["pk"])
        if obj is None:
            raise ApprovalError(f"{self.label}: the record no longer exists.")
        # Only snapshotted requests can detect concurrent edits; direct execution has no snapshot.
        before = getattr(approval_request, "before_snapshot", None)
        if before and self._current(obj, payload["changes"]) != before:
            raise ApprovalError(f"{self.label}: the record was changed after this request was raised.")

    def execute(self, approval_request, context: ExecutionContext):
        """Re-validate (the direct, no-policy path skips the services' validate step), convert JSON values back to
        field types, then ``apply``. Model validation errors become ApprovalError so they show as a failure reason."""
        payload = approval_request.payload
        self.validate(approval_request)
        obj = self._record(approval_request.tenant, payload["pk"])
        changes = {name: obj._meta.get_field(name).to_python(value) for name, value in payload["changes"].items()}
        try:
            self.apply(obj, changes, context)
        except ValidationError as err:
            raise ApprovalError("; ".join(err.messages)) from err
        return {"pk": obj.pk, "changed": sorted(changes)}

    def review_context(self, approval_request):
        """Current/Proposed rows labelled with the fields' verbose names."""
        labels = {f.name: str(f.verbose_name).capitalize() for f in self.model._meta.fields}
        return {"rows": diff_rows(approval_request.before_snapshot or {}, approval_request.payload["changes"], labels)}
