"""``@approval_action``: put a service function behind maker-checker without writing a handler class.

This is the Command pattern. Calling the decorated function captures the call (its keyword arguments, made
JSON-safe) as an Approval Request when a policy applies, and runs the function only once the request is approved.
With no policy the function runs immediately, so the call site is the same either way.

The decorated function must take keyword-only arguments: ``tenant``, ``maker`` and ``context`` are supplied by the
framework; everything else is the payload and must survive a JSON round trip (``Decimal``/dates arrive as strings).

.. code-block:: python

    @approval_action(
        "balance_update",
        label="Balance Update",
        category="Balances",
        lock_key=lambda p: f"{p['account_id']}:{p['balance_date']}",
        target_label=lambda p: f"Account {p['account_id']} on {p['balance_date']}",
    )
    def update_balance(*, tenant, maker, context, account_id, balance_date, new_balance):
        ...
        return {"account_id": account_id}

    outcome = update_balance(tenant=tenant, maker=request.user, request=request,
                             justification="Bank restated the balance",
                             account_id=1, balance_date="2026-09-30", new_balance="100.00")
    if outcome.pending:
        ...  # outcome.approval_request.reference
"""

from dataclasses import dataclass
from functools import wraps
from typing import Any, Callable, Optional

from django_maker_checker import services
from django_maker_checker.registry import (  # to_payload is re-exported here, where it has always been
    ActionHandler,
    ExecutionContext,
    Scope,
    diff_rows,
    register_handler,
    to_payload,
)


@dataclass
class Outcome:
    """Result of calling an approval action: either pending approval or already applied."""

    approval_request: Optional[Any] = None
    result: Any = None

    @property
    def pending(self):
        return self.approval_request is not None


class FunctionHandler(ActionHandler):
    """ActionHandler whose behaviour comes from a function plus optional hook callables.

    Each hook argument of ``@approval_action`` replaces the matching ``ActionHandler`` method; omitted hooks keep the
    neutral defaults (no label, empty snapshot, no scopes, every change matches, nothing to re-validate).
    """

    def __init__(
        self,
        func,
        *,
        action_type,
        label,
        category,
        lock_key,
        description="",
        icon="shield",
        sub_options=(),
        scopes=(Scope.GLOBAL,),
        review_template="approvals/review/generic.html",
        target_label=None,
        snapshot_before=None,
        scope_keys=None,
        matches=None,
        validate=None,
        lock_keys=None,
        validate_conditions=None,
        describe_conditions=None,
    ):
        self.func = func
        self.action_type = action_type
        self.label = label
        self.category = category
        self.description = description
        self.icon = icon
        self.sub_options = tuple(sub_options)
        self.scopes = tuple(scopes)
        self.review_template = review_template
        self._lock_key = lock_key
        self._target_label = target_label
        self._snapshot_before = snapshot_before
        self._scope_keys = scope_keys
        self._matches = matches
        self._validate = validate
        self._lock_keys = lock_keys
        self._validate_conditions = validate_conditions
        self._describe_conditions = describe_conditions

    @property
    def origin(self):
        # All decorated functions share this class, so the function (not the class) identifies the definition.
        return f"{self.func.__module__}.{self.func.__qualname__}"

    def lock_key(self, payload):
        return str(self._lock_key(payload))

    def target_label(self, payload):
        return str(self._target_label(payload)) if self._target_label else ""

    def snapshot_before(self, tenant, payload):
        return to_payload(self._snapshot_before(tenant, payload)) if self._snapshot_before else {}

    def scope_keys(self, tenant, payload):
        return list(self._scope_keys(tenant, payload)) if self._scope_keys else []

    def matches(self, policy, payload):
        return self._matches(policy, payload) if self._matches else True

    def lock_keys(self, payload):
        return [str(key) for key in self._lock_keys(payload)] if self._lock_keys else []

    def validate_conditions(self, conditions):
        return list(self._validate_conditions(conditions)) if self._validate_conditions else []

    def describe_conditions(self, conditions):
        if self._describe_conditions:
            return self._describe_conditions(conditions)
        return super().describe_conditions(conditions)

    def validate(self, approval_request):
        if self._validate:
            self._validate(approval_request)

    def execute(self, approval_request, context: ExecutionContext):
        """Replay the captured call. ``maker`` is the person who asked for the change, so the change is attributed
        to them; the final checker is ``context.actor``."""
        return self.func(
            tenant=approval_request.tenant,
            maker=approval_request.maker,
            context=context,
            **approval_request.payload,
        )

    def review_context(self, approval_request):
        return {"rows": diff_rows(approval_request.before_snapshot or {}, approval_request.payload)}


def approval_action(action_type: str, *, label: str, category: str, lock_key: Callable[[dict], str], **options):
    """Register ``func`` as a maker-checker action and return a callable that routes through approval."""

    def decorator(func):
        handler = register_handler(
            FunctionHandler(func, action_type=action_type, label=label, category=category, lock_key=lock_key, **options)
        )

        @wraps(func)
        def submit(*, tenant, maker, justification="", request=None, **payload):
            """Call the action: returns an ``Outcome`` that is pending (a request was raised) or holds the result."""
            approval_request, result = services.submit_or_execute(
                action_type,
                tenant=tenant,
                maker=maker,
                payload=to_payload(payload),
                justification=justification,
                request=request,
            )
            return Outcome(approval_request, result)

        submit.handler = handler
        submit.apply = func  # the undecorated function, for callers that must bypass approval (tests, admin tools)
        return submit

    return decorator
