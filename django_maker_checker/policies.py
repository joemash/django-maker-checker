"""Creating, validating and changing approval policies.

All policy rules live here so any host UI (or API) gets the same validation: approvers must belong to the tenant
and hold the action's approve permission, every step of an approval flow needs an approver, and an SLA needs an
escalation backup. A host form only parses its input into a ``PolicySpec`` and calls ``save_policy``.

Every change is recorded as an ``ApprovalPolicyRevision`` (and sent to the host's audit log) in the same transaction,
so the history of a rule (who changed what, when and why) can't be skipped by any caller.
"""

from dataclasses import dataclass, field
from typing import List, Optional

from django.db import transaction
from django.db.models import Max
from django.utils import translation
from django.utils.translation import gettext as _

from django_maker_checker import audit
from django_maker_checker.adapter import AuditRecord
from django_maker_checker.conf import get_adapter
from django_maker_checker.constants import PolicyChange, RiskLevel, SlaUnit, WorkflowType
from django_maker_checker.models import ApprovalPolicy, ApprovalPolicyApprover, ApprovalPolicyRevision
from django_maker_checker.permissions import approver_choices
from django_maker_checker.registry import ActionHandler, Scope, get_handler


class PolicyError(ValueError):
    """The policy spec is invalid; ``messages`` are safe to show."""

    def __init__(self, messages):
        self.messages = list(messages)
        super().__init__(" ".join(self.messages))


@dataclass
class PolicySpec:
    """What a host UI or API asks a rule to become. Approvers are tokens (``u:<id>`` / ``r:<id>``) so specs can be
    built straight from form posts; ``save_policy`` resolves and validates them against the tenant."""

    workflow_type: str = WorkflowType.SINGLE
    approvers: List[str] = field(default_factory=list)  # "u:<id>" / "r:<id>"; ordered steps for approval flows
    require_all: bool = False
    required_approvals: Optional[int] = None  # Multiple Approvers: how many must approve (N of M)
    is_enabled: bool = True
    risk_level: str = RiskLevel.MEDIUM
    sla_enabled: bool = False
    sla_value: Optional[int] = None
    sla_unit: Optional[str] = None
    escalation: str = ""  # "u:<id>" / "r:<id>"
    conditions: Optional[dict] = None


def policy_for(tenant, action_type, scope_key=""):
    """The saved policy for this tenant/action/scope, or an unsaved one with defaults."""
    policy = ApprovalPolicy.objects.filter(tenant=tenant, action_type=action_type, scope_key=scope_key).first()
    return policy or ApprovalPolicy(tenant=tenant, action_type=action_type, scope_key=scope_key, is_enabled=True)


def add_scoped_policy(tenant, action_type, scope_key, by):
    """Create an (unconfigured) policy for a scope, e.g. when a trigger is added from a trigger bank."""
    handler = get_handler(action_type)
    if not scope_key or not handler.supports(Scope.SCOPED):
        raise PolicyError([_("%(action)s cannot have per-scope rules.") % {"action": handler.label}])
    with transaction.atomic():
        policy, created = ApprovalPolicy.objects.get_or_create(
            tenant=tenant, action_type=action_type, scope_key=scope_key, defaults={"created_by": by, "updated_by": by}
        )
        if created:
            _record(policy, PolicyChange.CREATED, by, before=None, after=describe(policy))
    return policy


def _resolver(policy):
    """Build a token → (user, role) lookup over the users and roles eligible to approve this action.

    Resolving only against the eligible set means one check covers several rules at once: approvers must exist,
    belong to the policy's tenant, be active and hold the approve permission.
    """
    users, roles = approver_choices(policy.tenant, policy.action_type)
    by_token = {f"u:{u.pk}": (u, None) for u in users}
    by_token.update({f"r:{r.pk}": (None, r) for r in roles})
    label = get_handler(policy.action_type).approve_permission_label

    def resolve(token, escalation=False):
        if token not in by_token:
            if escalation:
                message = _(
                    "The escalation backup is not a valid approver: approvers must belong to this tenant and hold "
                    "the '%(permission)s' permission."
                )
            else:
                message = _(
                    "An approver is not a valid approver: approvers must belong to this tenant and hold the "
                    "'%(permission)s' permission."
                )
            raise PolicyError([message % {"permission": label}])
        return by_token[token]

    return resolve


def validate(policy, spec: PolicySpec):
    """Return ``(approvers, escalation)`` resolved to ``(user, role)`` pairs, or raise PolicyError.

    Shape errors (unknown choices, wrong approver count, incomplete SLA) are collected and reported together; only
    then are tokens resolved against the tenant, which fails on the first ineligible approver.
    """
    tokens = _approver_tokens(spec)
    errors = [
        *_choice_errors(spec),
        *_approver_errors(spec, tokens),
        *_sla_errors(spec),
        *_condition_errors(policy, spec),
    ]
    if errors:
        raise PolicyError(errors)

    resolve = _resolver(policy)
    approvers = [resolve(token) for token in tokens]
    escalation = resolve(spec.escalation, escalation=True) if spec.escalation else (None, None)
    return approvers, escalation


def _condition_errors(policy, spec):
    """The action's own checks on the rule's ``conditions`` (``ActionHandler.validate_conditions``)."""
    if spec.conditions is None:
        return []
    return list(get_handler(policy.action_type).validate_conditions(spec.conditions))


def _approver_tokens(spec):
    """Approval flows keep every step as given (order matters; blanks and repeated users are errors). Other workflows
    ignore blanks and
    repeated users; Multiple Approvers keeps a repeated role, which asks for that many different members of it
    ("two people from Finance"), since one person can only fill one slot."""
    if spec.workflow_type == WorkflowType.SEQUENTIAL:
        return list(spec.approvers)
    tokens = [token for token in spec.approvers if token]
    if spec.workflow_type != WorkflowType.MULTI:
        return list(dict.fromkeys(tokens))
    seen_users = set()
    kept = []
    for token in tokens:
        if token.startswith("u:"):
            if token in seen_users:
                continue
            seen_users.add(token)
        kept.append(token)
    return kept


def _choice_errors(spec):
    if spec.workflow_type not in dict(WorkflowType.CHOICES):
        yield _("Unknown workflow type.")
    if spec.risk_level not in dict(RiskLevel.CHOICES):
        yield _("Unknown risk level.")


def _approver_errors(spec, tokens):
    if spec.workflow_type == WorkflowType.SEQUENTIAL and (not tokens or not all(tokens)):
        yield _("Every step of the approval flow needs an approver.")
    if spec.workflow_type == WorkflowType.SEQUENTIAL and _repeats_a_user(tokens):
        yield _("A person can approve only one step of an approval flow: choose someone else for the other steps.")
    if spec.workflow_type == WorkflowType.SINGLE and len(tokens) != 1:
        yield _("Select exactly one approver.")
    if spec.workflow_type == WorkflowType.MULTI and len(tokens) < 2:
        yield _("Select at least two approvers, or use Single Approver.")
    yield from _required_approvals_errors(spec, tokens)


def _repeats_a_user(tokens):
    """Whether a user is named more than once. One person acts once per round, so a flow naming them on two steps
    could never finish; a role on two steps is fine, since different members fill them."""
    users = [token for token in tokens if token.startswith("u:")]
    return len(users) != len(set(users))


def _required_approvals_errors(spec, tokens):
    if spec.required_approvals is None:
        return
    if spec.workflow_type != WorkflowType.MULTI:
        yield _("A number of required approvals only applies to Multiple Approvers.")
    elif spec.require_all:
        yield _("Choose either all approvers or a number of required approvals, not both.")
    elif not 1 <= spec.required_approvals <= len(tokens):
        yield _("Required approvals must be between 1 and the number of approvers (%(count)s).") % {
            "count": len(tokens)
        }


def _sla_errors(spec):
    if not spec.sla_enabled:
        return
    if not spec.sla_value or spec.sla_value < 1:
        yield _("Enter a time limit of at least 1.")
    if spec.sla_unit not in SlaUnit.TO_MINUTES:
        yield _("Choose a time unit.")
    if not spec.escalation:
        yield _("Choose an escalation backup approver for the SLA rule.")


@transaction.atomic
def save_policy(policy, spec: PolicySpec, by, reason=""):
    """Validate ``spec`` and apply it to ``policy`` (saved or new), recording a revision when anything changed.

    In-flight requests keep their snapshot.
    """
    approvers, escalation = validate(policy, spec)
    creating = policy.pk is None
    if not creating:
        # Serialise concurrent edits of one rule so each gets its own revision number.
        ApprovalPolicy.objects.select_for_update().filter(pk=policy.pk).first()
    before = None if creating else describe(policy)

    _apply_spec(policy, spec, escalation)
    policy.updated_by = by
    if creating:
        policy.created_by = by
    policy.save()
    _replace_approvers(policy, approvers)

    after = describe(policy)
    if creating:
        _record(policy, PolicyChange.CREATED, by, before=None, after=after, reason=reason)
    elif after != before:
        _record(policy, _change_type(before, after), by, before=before, after=after, reason=reason)
    return policy


def _apply_spec(policy, spec, escalation):
    """Copy the validated spec onto the policy. ``require_all`` only means something for multi-approver rules, and
    a switched-off SLA keeps its last value so turning it back on restores it."""
    policy.is_enabled = spec.is_enabled
    policy.workflow_type = spec.workflow_type
    multi = spec.workflow_type == WorkflowType.MULTI
    policy.require_all = bool(spec.require_all) and multi
    policy.required_approvals = spec.required_approvals if multi else None
    policy.risk_level = spec.risk_level
    policy.sla_enabled = spec.sla_enabled
    if spec.sla_enabled:
        policy.sla_value, policy.sla_unit = spec.sla_value, spec.sla_unit
    if spec.conditions is not None:
        policy.conditions = spec.conditions
    policy.escalation_user, policy.escalation_role = escalation


def _replace_approvers(policy, approvers):
    policy.approvers.all().delete()
    ApprovalPolicyApprover.objects.bulk_create(
        ApprovalPolicyApprover(policy=policy, order=order, approver_user=user, approver_role=role)
        for order, (user, role) in enumerate(approvers)
    )


@transaction.atomic
def delete_policy(policy, by=None, reason=""):
    """Remove a policy, recording its last state. Requests already raised under it keep their snapshot."""
    _record(policy, PolicyChange.DELETED, by, before=describe(policy), after=None, reason=reason)
    policy.delete()


# --- history ------------------------------------------------------------------------------------

# Revisions store the rule in this display form and compare it across revisions, so its labels and values are kept
# in English whatever language is active when a rule is saved; a UI may translate them when showing a history.
ENABLED = "Rule enabled"


def describe(policy):
    """The rule in display form, ``{field label: text}``, as stored on revisions and shown in histories.

    Names are resolved at the time of the change rather than stored as ids, so a revision still reads correctly
    after an approver leaves or a role is renamed or deleted. The SLA backup only counts while the SLA is on.
    """
    with translation.override("en"):
        return _describe(policy)


def _describe(policy):
    return {
        ENABLED: "Yes" if policy.is_enabled else "No",
        "Workflow": _workflow_text(policy),
        "Approvers": _approvers_text(policy),
        "SLA": _sla_text(policy),
        "Escalation backup": _escalation_text(policy) if policy.sla_enabled else "—",
        "Risk level": str(dict(RiskLevel.CHOICES).get(policy.risk_level, policy.risk_level)),
        **_conditions_rows(policy),
    }


def _workflow_text(policy):
    text = str(dict(WorkflowType.CHOICES).get(policy.workflow_type, policy.workflow_type))
    if policy.workflow_type == WorkflowType.MULTI:
        count = policy.approvers.count() if policy.pk else 0
        if policy.required_approvals:
            text += f" ({policy.required_approvals} of {count} must approve)"
        else:
            text += " (all must approve)" if policy.require_all else " (any one)"
    return text


def _approvers_text(policy):
    """Approval-flow steps joined with arrows (the order matters); other workflows as a plain list."""
    labels = [a.approver_label for a in policy.ordered_approvers()] if policy.pk else []
    separator = " → " if policy.workflow_type == WorkflowType.SEQUENTIAL else ", "
    return separator.join(labels) or "—"


def _sla_text(policy):
    if not policy.sla_enabled:
        return "Off"
    unit = str(dict(SlaUnit.CHOICES).get(policy.sla_unit, policy.sla_unit)).lower()
    return f"{policy.sla_value} {unit}"


def _escalation_text(policy):
    adapter = get_adapter()
    if policy.escalation_user_id:
        return adapter.user_label(policy.escalation_user)
    if policy.escalation_role_id:
        return adapter.role_label(policy.escalation_role)
    return "—"


def _conditions_rows(policy):
    """The action's own description of the rule's conditions (``ActionHandler.describe_conditions``). A rule whose
    action is no longer registered falls back to the plain form, so its history can still be written."""
    try:
        handler = get_handler(policy.action_type)
    except LookupError:
        handler = ActionHandler()
    return {str(label): str(text) for label, text in handler.describe_conditions(policy.conditions or {}).items()}


def _diff(before, after):
    """Changed fields between two ``describe()`` results, as display rows; missing side shows as "—"."""
    before, after = before or {}, after or {}
    return [
        {"field": key, "before": before.get(key, "—"), "after": after.get(key, "—")}
        for key in dict.fromkeys([*before, *after])
        if before.get(key) != after.get(key)
    ]


def _change_type(before, after):
    """Classify an edit: toggling only the enabled switch is ENABLED/DISABLED (the changes auditors look for most);
    anything else, including a toggle combined with other edits, is UPDATED."""
    changed = {row["field"] for row in _diff(before, after)}
    if changed == {ENABLED}:
        return PolicyChange.ENABLED if after[ENABLED] == "Yes" else PolicyChange.DISABLED
    return PolicyChange.UPDATED


# Revision change types mapped to the host audit log's action vocabulary; enable/disable/update are all "updated".
_AUDIT_ACTIONS = {PolicyChange.CREATED: "created", PolicyChange.DELETED: "deleted"}


def _record(policy, change_type, actor, before, after, reason=""):
    """Write the next revision of the rule and send it to the host audit log.

    Versions are numbered per tenant, action and scope (not per policy row), so numbering continues if a deleted
    rule is created again. Called inside the caller's transaction; ``save_policy`` locks the policy row first so
    concurrent edits get distinct versions (the unique constraint is the backstop).
    """
    key = {"tenant": policy.tenant, "action_type": policy.action_type, "scope_key": policy.scope_key}
    revision = ApprovalPolicyRevision.objects.create(
        policy=policy,
        version=current_version(**key) + 1,
        change_type=change_type,
        snapshot=after if after is not None else before,
        changes=_diff(before, after),
        reason=(reason or "").strip(),
        actor=actor,
        **key,
    )
    audit.send(
        AuditRecord(
            action=_AUDIT_ACTIONS.get(change_type, "updated"),
            resource_type="ApprovalPolicy",
            resource_id=str(policy.pk),
            tenant_id=policy.tenant_id,
            actor_id=getattr(actor, "pk", None),
            before=before,
            after=after,
            metadata={
                "action_type": policy.action_type,
                "scope_key": policy.scope_key,
                "version": revision.version,
                "change_type": change_type,
                "reason": revision.reason,
            },
        )
    )
    return revision


def current_version(tenant, action_type, scope_key=""):
    """Latest revision number of a rule (0 if it has never been saved)."""
    revisions = ApprovalPolicyRevision.objects.filter(tenant=tenant, action_type=action_type, scope_key=scope_key)
    return revisions.aggregate(v=Max("version"))["v"] or 0


def policy_summary(policy):
    """``(is_active, is_configured)`` for list pages."""
    configured = bool(policy.pk) and policy.is_configured()
    return policy.is_enabled and configured, configured
