"""Registry of actions that can be put behind maker-checker.

Each sensitive action is described by an ``ActionHandler``: how to build its lock key and
"before" snapshot, how to re-validate it when approved, how to apply it, and which template shows
its change in the review drawer. Adding a new guarded action means writing one handler.
"""

import json
from dataclasses import dataclass, field
from typing import Optional

from django.core.serializers.json import DjangoJSONEncoder
from django.http import HttpRequest


class Scope:
    GLOBAL = "global"  # one rule for the whole tenant
    SCOPED = "scoped"  # rules per host-defined scope (see ``ActionHandler.scope_keys``)


@dataclass
class ExecutionContext:
    """What a handler gets when applying an approved change."""

    actor: object  # the checker whose approval completed the request
    request: Optional[HttpRequest] = None
    extra: dict = field(default_factory=dict)


class ActionHandler:
    """Describes one guarded action: how its change is captured, identified, shown and applied (Strategy).

    Handlers are stateless singletons held in the registry. The services and the host UI only use this interface, so
    adding an action never touches the engine. Subclass it directly for actions that don't fit ``@approval_action``
    or ``ModelChangeHandler``.

    Class attributes describe the action for UIs: ``label``, ``category``, ``description``, ``icon`` and
    ``sub_options``. ``scopes`` says where rules may be configured, and ``review_template`` names a host template
    for the review panel.
    """

    action_type: str = ""
    label: str = ""
    category: str = ""
    description: str = ""
    icon: str = "shield"
    sub_options: tuple = ()
    scopes: tuple = (Scope.GLOBAL,)
    review_template: str = "approvals/review/generic.html"

    @property
    def origin(self) -> str:
        """Where the action is defined; one action type may only come from one place."""
        return f"{type(self).__module__}.{type(self).__qualname__}"

    @property
    def approve_permission(self) -> str:
        """Permission codename a checker needs for this action, checked through the adapter."""
        return f"approve_{self.action_type}"

    @property
    def approve_permission_label(self) -> str:
        # Stored as a permission name by hosts (like Django's own "Can add …"), so it stays in English.
        return f"Approve {self.label}"

    def supports(self, scope: str) -> bool:
        """Whether rules for this action may be configured at ``scope`` (tenant-wide and/or per scope)."""
        return scope in self.scopes

    # --- request construction ------------------------------------------------------------------

    def lock_key(self, payload: dict) -> str:
        """Identifies the item the change affects. Only one open request may exist per tenant, action and key, so two
        makers can't queue conflicting changes to the same item."""
        raise NotImplementedError

    def lock_keys(self, payload: dict) -> list:
        """Items the change affects that no other open request may claim, whatever its action type.

        Use it when one request touches several items (e.g. every transaction in a manual match), or items that other
        actions also change. These locks are taken in addition to ``lock_key``, and are what
        ``queries.open_requests_for`` looks up, e.g. to badge items that are waiting for approval. Default: none.
        """
        return []

    def target_label(self, payload: dict) -> str:
        """Human name of the item, shown in queues and notifications. Stored on the request, so it outlives the
        item."""
        return ""

    def snapshot_before(self, tenant, payload: dict) -> dict:
        """The item's current state, captured at submission for the review panel's "Current" column and for detecting
        changes made between submission and approval."""
        return {}

    def scope_keys(self, tenant, payload: dict) -> list:
        """Scopes the change belongs to, most specific first; a scoped rule for any of them wins over the
        tenant-wide rule. Keys are host-defined strings, e.g. ``"group:12"``."""
        return []

    def matches(self, policy, payload: dict) -> bool:
        """Whether ``policy.conditions`` select this payload. Default: every change is covered."""
        return True

    def validate_conditions(self, conditions: dict) -> list:
        """Problems with a rule's ``conditions`` for this action, as messages to show; empty when valid.

        ``policies.save_policy`` reports them with the rule's other errors, so every screen and API enforces them.
        """
        return []

    def describe_conditions(self, conditions: dict) -> dict:
        """A rule's ``conditions`` in display form, ``{label: text}``, for rule history.

        Revisions store this, so keep labels stable. Default: one "Conditions" row listing ``key=value`` pairs.
        """
        pairs = sorted((conditions or {}).items())
        return {"Conditions": ", ".join(f"{name}={value}" for name, value in pairs) or "—"}

    # --- approval ------------------------------------------------------------------------------

    def validate(self, approval_request) -> None:
        """Re-check that the change can still be applied; raise ApprovalError if not."""

    def execute(self, approval_request, context: ExecutionContext) -> dict:
        """Apply the change and return a JSON-safe result stored on the request.

        Runs inside a transaction: raising rolls back and marks the request FAILED (it can be retried). Also called
        directly, with a stand-in request, when no policy applies, so it must not assume an approval took place.
        """
        raise NotImplementedError

    # --- presentation --------------------------------------------------------------------------

    def review_context(self, approval_request) -> dict:
        """Data for ``review_template``. The default is a generic Current/Proposed table of snapshot vs payload."""
        return {"rows": diff_rows(approval_request.before_snapshot, approval_request.payload)}


def to_payload(values: dict) -> dict:
    """The JSON form a payload is stored in; raises TypeError naming the offending value.

    Payloads go through this round trip on both paths (approved later, or applied immediately), so the action's
    function always receives the same JSON values (``Decimal`` and dates as strings) whether or not approval was
    required. That keeps behaviour identical when a tenant turns a rule on or off.
    """
    try:
        return json.loads(json.dumps(values, cls=DjangoJSONEncoder))
    except TypeError as err:
        raise TypeError(f"Maker-checker payloads must be JSON-serialisable: {err}") from err


def diff_rows(before: dict, after: dict, labels: Optional[dict] = None):
    """Rows for the generic side-by-side "Current / Proposed" panel."""
    labels = labels or {}
    keys = list(dict.fromkeys([*before.keys(), *after.keys()]))
    return [
        {
            "label": labels.get(key, key.replace("_", " ").title()),
            "current": before.get(key, "—"),
            "proposed": after.get(key, "—"),
            "changed": before.get(key) != after.get(key),
        }
        for key in keys
    ]


# action_type -> handler. Filled at import time by @register / @approval_action from the host's AppConfig.ready().
_registry: dict = {}


def register(handler_cls):
    """Class decorator: instantiate and register an ActionHandler subclass."""
    register_handler(handler_cls())
    return handler_cls


def register_handler(handler):
    """Register a handler instance. Re-registering from the same place is allowed (module reloads); a second
    definition of the same action type elsewhere is an error, since two handlers would silently compete."""
    if not handler.action_type:
        raise ValueError(f"{type(handler).__name__} has no action_type")
    existing = _registry.get(handler.action_type)
    if existing is not None and existing.origin != handler.origin:
        raise ValueError(f"Action type '{handler.action_type}' is already registered by {existing.origin}")
    _registry[handler.action_type] = handler
    return handler


def unregister(action_type):
    """Remove a handler; meant for tests that register throwaway actions."""
    _registry.pop(action_type, None)


def get_handler(action_type: str) -> ActionHandler:
    try:
        return _registry[action_type]
    except KeyError:
        raise LookupError(f"No maker-checker handler registered for '{action_type}'") from None


def all_handlers(scope: Optional[str] = None):
    """Registered handlers, sorted for display, optionally only those that support ``scope``."""
    handlers = sorted(_registry.values(), key=lambda h: (h.category, h.label))
    if scope:
        handlers = [h for h in handlers if h.supports(scope)]
    return handlers
