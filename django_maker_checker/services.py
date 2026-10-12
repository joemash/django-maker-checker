"""All writes to maker-checker state go through this module.

Hosts call ``submit_or_execute`` / ``decide`` / ``cancel`` and never touch ``ApprovalRequest.status`` directly.
Every operation that changes a request locks its row (``select_for_update``) so concurrent approvals can't execute
a change twice; notifications run on commit.
"""

import logging
from datetime import timedelta

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from django_maker_checker import audit, notify
from django_maker_checker.adapter import AuditRecord
from django_maker_checker.conf import counts_cache, get_adapter, role_model_name
from django_maker_checker.constants import Decision, EventType, RequestStatus, SlaState, WorkflowType
from django_maker_checker.exceptions import ApprovalError
from django_maker_checker.models import (
    ApprovalDecision,
    ApprovalDelegation,
    ApprovalEvent,
    ApprovalPolicy,
    ApprovalRequest,
    ApprovalStage,
    ApprovalStageApprover,
)
from django_maker_checker.permissions import (
    can_cancel,
    can_retry,
    can_view,
    decision_slot,
    eligible_checkers,
    has_approve_permission,
    has_decided,
    is_member_of,
)
from django_maker_checker.policies import current_version
from django_maker_checker.registry import ExecutionContext, Scope, all_handlers, get_handler

logger = logging.getLogger(__name__)


# --- policy lookup ------------------------------------------------------------------------------


def _usable(policy):
    # A policy with no approvers can't be satisfied, so it must not gate the action (it would block it forever).
    return policy is not None and policy.is_enabled and policy.is_configured()


def find_policy(tenant, action_type, payload):
    """The policy that governs ``payload``: the first matching scoped rule, otherwise the tenant-wide one.

    None when maker-checker is switched off for the tenant (``ApprovalsAdapter.is_enabled``).
    """
    if not get_adapter().is_enabled(tenant):
        return None
    handler = get_handler(action_type)
    candidates = []
    if handler.supports(Scope.SCOPED):
        candidates += handler.scope_keys(tenant, payload)
    if handler.supports(Scope.GLOBAL):
        candidates.append("")  # the tenant-wide rule
    policies = ApprovalPolicy.objects.filter(tenant=tenant, action_type=action_type, is_enabled=True)
    for scope_key in candidates:
        policy = policies.filter(scope_key=scope_key).first()
        if _usable(policy) and handler.matches(policy, payload):
            return policy
    return None


def requires_approval(tenant, action_type, payload=None) -> bool:
    """Whether the action would currently be held for approval; for UIs (e.g. to ask for a justification)."""
    return find_policy(tenant, action_type, payload or {}) is not None


# --- submission ---------------------------------------------------------------------------------


def parse_token(token):
    """``u:<id>`` / ``r:<id>`` to ``(user, role)``; unknown tokens give ``(None, None)``."""
    kind, _, raw_id = (token or "").partition(":")
    if not raw_id.isdigit():
        return None, None
    if kind == "u":
        return get_user_model().objects.filter(pk=int(raw_id)).first(), None
    if kind == "r":
        return None, apps.get_model(role_model_name()).objects.filter(pk=int(raw_id)).first()
    return None, None


def _stage_layout(policy):
    """Approver slots per stage, as ``(slots, require_all)``.

    SINGLE uses the first slot only; MULTI puts every slot in one stage (any one, or all with ``require_all``);
    SEQUENTIAL makes one stage per slot, in order.
    """
    approvers = policy.ordered_approvers()
    if policy.workflow_type == WorkflowType.SEQUENTIAL:
        return [([a], False) for a in approvers]
    if policy.workflow_type == WorkflowType.MULTI:
        return [(approvers, policy.require_all)]
    return [(approvers[:1], False)]


def _sla_due(start, sla_minutes):
    return start + timedelta(minutes=sla_minutes) if sla_minutes else None


def submit(action_type, *, tenant, maker, payload, justification="", policy=None):
    """Raise an approval request for ``payload`` under ``policy`` (looked up when not given).

    Refuses up front if any stage has no eligible checker other than the maker, so a request can never get stuck
    waiting for someone who can't approve it. The request, its stages (copied from the policy, only the first one
    started) and its "submitted" event are written in one transaction. The partial unique index on open requests
    turns a concurrent duplicate into a readable error that names the existing request. Checkers are notified on
    commit.
    """
    handler = get_handler(action_type)
    if not get_adapter().is_enabled(tenant):
        raise ApprovalError("Approvals are switched off for this organisation.")
    policy = policy or find_policy(tenant, action_type, payload)
    if policy is None:
        raise ApprovalError("No approval policy applies to this action.")

    layout = _stage_layout(policy)
    _ensure_every_stage_has_a_checker(tenant, action_type, layout, maker)

    now = timezone.now()
    lock_key = handler.lock_key(payload)
    try:
        with transaction.atomic():
            approval_request = ApprovalRequest.objects.create(
                tenant=tenant,
                action_type=action_type,
                policy=policy,
                policy_snapshot=policy.snapshot(),
                policy_version=current_version(tenant, action_type, policy.scope_key) or None,
                scope_key=policy.scope_key,
                target_label=handler.target_label(payload)[:255],
                lock_key=lock_key,
                payload=payload,
                before_snapshot=handler.snapshot_before(tenant, payload),
                justification=justification.strip(),
                risk_level=policy.risk_level,
                maker=maker,
                submitted_at=now,
            )
            _create_stages(approval_request, layout, now, policy.sla_minutes)
            target = f" for {approval_request.target_label}" if approval_request.target_label else ""
            _event(approval_request, EventType.CREATED, maker, f"{_name(maker)} submitted {handler.label}{target}.")
            transaction.on_commit(lambda: notify.request_submitted(approval_request))
    except IntegrityError:
        raise _already_open_error(tenant, action_type, lock_key) from None

    _bump(tenant.pk)
    _audit(approval_request, "created", maker, after={"status": approval_request.status})
    return approval_request


def _ensure_every_stage_has_a_checker(tenant, action_type, layout, maker):
    for number, (slots, _) in enumerate(layout, start=1):
        if not eligible_checkers(tenant, action_type, slots, exclude_user_ids=[maker.id]):
            step = f" for step {number}" if len(layout) > 1 else ""
            raise ApprovalError(
                f"No eligible approver other than you is configured{step}. "
                "Ask an administrator to update the approval configuration."
            )


def _create_stages(approval_request, layout, now, sla_minutes):
    """One stage per layout entry, copying the policy's approver slots. Only the first stage starts (and gets an SLA
    deadline) now; later ones start when the previous stage completes."""
    for order, (slots, require_all) in enumerate(layout):
        first = order == 0
        stage = ApprovalStage.objects.create(
            request=approval_request,
            order=order,
            require_all=require_all,
            started_at=now if first else None,
            due_at=_sla_due(now, sla_minutes) if first else None,
        )
        ApprovalStageApprover.objects.bulk_create(
            ApprovalStageApprover(stage=stage, approver_user_id=s.approver_user_id, approver_role_id=s.approver_role_id)
            for s in slots
        )


def _already_open_error(tenant, action_type, lock_key):
    """The error for a duplicate submission, naming the open request that blocks it."""
    existing = ApprovalRequest.objects.filter(
        tenant=tenant, action_type=action_type, lock_key=lock_key, status__in=RequestStatus.OPEN
    ).first()
    reference = f" ({existing.reference})" if existing else ""
    return ApprovalError(f"There is already an open approval request{reference} for this item.")


class _Direct:
    """Stand-in passed to ``handler.execute`` when no approval is required.

    It looks enough like an ApprovalRequest that handlers keep one code path. It has no ``before_snapshot``, which
    handlers use to tell the immediate path from the approved one.
    """

    def __init__(self, tenant, maker, action_type, payload):
        self.tenant = tenant
        self.tenant_id = tenant.pk
        self.maker = maker
        self.maker_id = maker.pk
        self.action_type = action_type
        self.payload = payload
        self.reference = None
        self.justification = ""


def submit_or_execute(action_type, *, tenant, maker, payload, justification="", request=None):
    """Create an approval request if a policy applies, otherwise apply the change now.

    Returns ``(approval_request, result)``; exactly one of them is set.
    """
    policy = find_policy(tenant, action_type, payload)
    if policy is None:
        handler = get_handler(action_type)
        direct = _Direct(tenant, maker, action_type, payload)
        return None, handler.execute(direct, ExecutionContext(maker, request))
    approval_request = submit(
        action_type, tenant=tenant, maker=maker, payload=payload, justification=justification, policy=policy
    )
    return approval_request, None


# --- decisions ----------------------------------------------------------------------------------


def _refusal_reason(user, approval_request):
    # The most specific explanation for why decision_slot() refused, for the error shown to the user.
    if approval_request.status != RequestStatus.PENDING:
        return f"This request is already {approval_request.get_status_display().lower()}."
    if user.id == approval_request.maker_id:
        return "You cannot approve or reject a request you raised."
    if has_decided(user, approval_request):
        return "You have already acted on this request."
    return "You are not an approver for the current step of this request."


def decide(request_id, user, decision, comment="", http_request=None):
    """Record ``user``'s approve/reject on the request's current stage and move the request on.

    The request row is locked for the whole decision, so concurrent checkers are serialised and each sees the
    other's effect. A rejection ends the request. An approval fills the user's slot. When that completes the stage,
    the request either moves to the next stage (which starts its own SLA window, and its checkers are notified) or,
    on the last stage, becomes APPROVED. The change is then applied by ``execute_approved`` *after* the decision
    commits, so a failure to apply can never undo the approval.
    """
    if decision not in (Decision.APPROVE, Decision.REJECT):
        raise ApprovalError("Unknown decision.")
    comment = (comment or "").strip()
    if decision == Decision.REJECT and not comment:
        raise ApprovalError("A reason is required to reject a request.")

    with transaction.atomic():
        approval_request = ApprovalRequest.objects.select_for_update().get(pk=request_id)
        slot = decision_slot(user, approval_request)
        if slot is None:
            raise ApprovalError(_refusal_reason(user, approval_request))
        stage = approval_request.active_stage
        ApprovalDecision.objects.create(
            request=approval_request,
            stage=stage,
            actor=user,
            on_behalf_of=slot.on_behalf_of,
            decision=decision,
            comment=comment,
        )
        if decision == Decision.REJECT:
            _reject(approval_request, stage, slot, user, comment)
            fully_approved = False
        else:
            fully_approved = _approve(approval_request, stage, slot, user, comment)

    _bump(approval_request.tenant_id)
    _audit(approval_request, "updated", user, after={"status": approval_request.status, "decision": decision})
    if fully_approved:
        approval_request = execute_approved(approval_request.pk, user, http_request)
        notify.request_decided(approval_request)
    return approval_request


def _who(user, slot):
    """Who acted, for timeline messages: "Chris", or "Chris on behalf of Carol" when delegated."""
    if slot.on_behalf_of:
        return f"{_name(user)} on behalf of {_name(slot.on_behalf_of)}"
    return _name(user)


def _reject(approval_request, stage, slot, user, comment):
    """End the request as rejected; the maker is told the reason after commit."""
    try:
        approval_request.reject(by=user, reason=comment)
    except ValidationError as err:
        raise ApprovalError(" ".join(err.messages)) from None
    approval_request.save()
    stage.completed_at = timezone.now()
    stage.save(update_fields=["completed_at"])
    _event(approval_request, EventType.REJECTED, user, f"Rejected by {_who(user, slot)}. Reason: {comment}")
    transaction.on_commit(lambda: notify.request_decided(approval_request))


def _approve(approval_request, stage, slot, user, comment):
    """Fill the user's slot and move the request on. Returns True when this was the final approval."""
    now = timezone.now()
    approver = slot.approver
    approver.satisfied_at = now
    approver.satisfied_by = user
    approver.save(update_fields=["satisfied_at", "satisfied_by"])
    if comment:
        _event(approval_request, EventType.COMMENT, user, comment)

    if not _stage_complete(stage, approver):
        done, total = stage_progress_counts(stage)
        message = f"Approved by {_who(user, slot)} ({done} of {total} required approvals)."
        _event(approval_request, EventType.STAGE_APPROVED, user, message)
        return False

    stage.completed_at = now
    stage.save(update_fields=["completed_at"])
    next_stage = approval_request.stages.filter(order=stage.order + 1).first()
    if next_stage is not None:
        _start_stage(approval_request, next_stage, now)
        message = f"Step {stage.order + 1} approved by {_who(user, slot)}; routed to step {next_stage.order + 1}."
        _event(approval_request, EventType.STAGE_APPROVED, user, message)
        transaction.on_commit(lambda: notify.request_submitted(approval_request))
        return False

    approval_request.approve(by=user)
    approval_request.save()
    _event(approval_request, EventType.APPROVED, user, f"Approved by {_who(user, slot)}.")
    return True


def _start_stage(approval_request, stage, now):
    """Make ``stage`` the active one, with its own SLA window starting now."""
    stage.started_at = now
    stage.due_at = _sla_due(now, approval_request.policy_snapshot.get("sla_minutes"))
    stage.save(update_fields=["started_at", "due_at"])
    approval_request.current_stage = stage.order
    approval_request.save()


def stage_progress_counts(stage):
    """``(approvals done, approvals required)`` among the stage's regular approvers.

    Escalation slots are excluded: they complete a stage on their own and aren't part of "N of M approvals".
    """
    regular = [a for a in stage.approvers.all() if not a.is_escalation]
    return sum(1 for a in regular if a.satisfied_at), len(regular)


def _stage_complete(stage, approver):
    """A stage completes on the first approval unless it requires all approvers. An escalation approver's approval
    completes it regardless, since escalation exists to unblock a stalled step."""
    if approver.is_escalation or not stage.require_all:
        return True
    done, total = stage_progress_counts(stage)
    return done >= total


def execute_approved(request_id, actor, http_request=None):
    """Apply an approved change. A failure leaves the decision intact and marks the request failed.

    The handler's ``validate`` runs first, inside the same locked transaction as ``execute``, so the change is
    re-checked against the current state of the item immediately before it is applied. On any exception the
    transaction rolls back and a fresh one records FAILED with the error; ``ApprovalError`` messages are expected
    and aren't logged as crashes.
    """
    approval_request = ApprovalRequest.objects.get(pk=request_id)
    handler = get_handler(approval_request.action_type)
    try:
        with transaction.atomic():
            approval_request = ApprovalRequest.objects.select_for_update().get(pk=request_id)
            if approval_request.status != RequestStatus.APPROVED:
                return approval_request
            handler.validate(approval_request)
            result = handler.execute(approval_request, ExecutionContext(actor=actor, request=http_request))
            approval_request.mark_executed(result)
            approval_request.save()
            _event(approval_request, EventType.EXECUTED, None, "Change applied.")
    except Exception as exc:  # noqa: BLE001 - any failure must be recorded, not lost
        if not isinstance(exc, ApprovalError):
            logger.exception("Executing approval request %s failed", request_id)
        with transaction.atomic():
            approval_request = ApprovalRequest.objects.select_for_update().get(pk=request_id)
            if approval_request.status == RequestStatus.APPROVED:
                approval_request.mark_failed(exc)
                approval_request.save()
                _event(approval_request, EventType.FAILED, None, f"Applying the change failed: {exc}")
    _bump(approval_request.tenant_id)
    _audit(approval_request, "updated", actor, after={"status": approval_request.status})
    return approval_request


def retry(request_id, user, http_request=None):
    """Re-apply a FAILED request (administrators only); the original approval stands, so no new decision is needed."""
    with transaction.atomic():
        approval_request = ApprovalRequest.objects.select_for_update().get(pk=request_id)
        if not can_retry(user, approval_request):
            raise ApprovalError("Only approval administrators can retry a failed request.")
        approval_request.retry()
        approval_request.save()
        _event(approval_request, EventType.RETRIED, user, f"{_name(user)} retried applying the change.")
    return execute_approved(request_id, user, http_request)


def cancel(request_id, user, reason=""):
    """Withdraw a pending request: by its maker, or by an administrator (the maker is then told who cancelled it)."""
    with transaction.atomic():
        approval_request = ApprovalRequest.objects.select_for_update().get(pk=request_id)
        if not can_cancel(user, approval_request):
            raise ApprovalError("You cannot cancel this request.")
        approval_request.cancel(by=user)
        approval_request.save()
        by_maker = user.id == approval_request.maker_id
        message = f"Cancelled by {'the maker' if by_maker else _name(user)}."
        if reason.strip():
            message += f" Reason: {reason.strip()}"
        _event(approval_request, EventType.CANCELLED, user, message)
        transaction.on_commit(lambda: notify.request_cancelled(approval_request, by_maker=by_maker))
    _bump(approval_request.tenant_id)
    _audit(approval_request, "updated", user, after={"status": approval_request.status})
    return approval_request


def add_comment(request_id, user, text):
    """Add a note to the request's timeline; anyone who may view the request may comment."""
    text = (text or "").strip()
    if not text:
        raise ApprovalError("Comment cannot be empty.")
    approval_request = ApprovalRequest.objects.get(pk=request_id)
    if not can_view(user, approval_request):
        raise ApprovalError("You cannot comment on this request.")
    _event(approval_request, EventType.COMMENT, user, text)
    return approval_request


def record_view(approval_request, user):
    """Log the first time each potential checker opens a pending request."""
    if approval_request.status != RequestStatus.PENDING or user.id == approval_request.maker_id:
        return
    if decision_slot(user, approval_request) is None:
        return
    if approval_request.events.filter(type=EventType.VIEWED, actor=user).exists():
        return
    _event(approval_request, EventType.VIEWED, user, f"Opened by {_name(user)}.")


# --- delegation ---------------------------------------------------------------------------------


def delegate(*, delegator, delegate_user, starts_at, ends_at, action_types=(), reason="", created_by=None):
    """Create a delegation after checking the delegator may approve and both users share a tenant."""
    tenant = get_adapter().tenant_of(delegator)
    if tenant is None:
        raise ApprovalError("You are not a member of a tenant.")
    if not any(has_approve_permission(delegator, t) for t in action_types or _all_action_types()):
        raise ApprovalError("Only approvers can delegate approvals.")
    if not is_member_of(delegate_user, tenant.pk):
        raise ApprovalError("The delegate must be an active member of your organisation.")
    delegation = ApprovalDelegation(
        tenant=tenant,
        delegator=delegator,
        delegate=delegate_user,
        starts_at=starts_at,
        ends_at=ends_at,
        action_types=list(action_types),
        reason=reason,
        created_by=created_by or delegator,
    )
    try:
        delegation.full_clean(exclude=["tenant", "delegator", "delegate", "created_by"])
    except ValidationError as err:
        raise ApprovalError(" ".join(err.messages)) from None
    delegation.save()
    _audit_delegation(delegation, created_by or delegator, "created")
    return delegation


def revoke_delegation(delegation, user):
    """End a delegation now (by its delegator or whoever created it); past decisions made under it are kept."""
    if user.pk not in (delegation.delegator_id, delegation.created_by_id):
        raise ApprovalError("You cannot end this delegation.")
    if not delegation.revoked_at:
        delegation.revoked_at = timezone.now()
        delegation.save(update_fields=["revoked_at"])
        _audit_delegation(delegation, user, "updated")
    return delegation


def _all_action_types():
    return [h.action_type for h in all_handlers()]


# --- SLA ----------------------------------------------------------------------------------------


def process_sla(now=None):
    """Send SLA reminders and escalate overdue stages. Safe to run concurrently and repeatedly.

    Each candidate stage is handled in its own transaction under ``SELECT … FOR UPDATE SKIP LOCKED``, so parallel runs
    split the work instead of blocking or double-processing. A stage is re-checked after locking, because it may have
    been decided in the meantime. Escalation (deadline passed) takes precedence over the reminder, and
    ``sla_warned_at`` / ``escalated_at`` make each happen once. Tenants with maker-checker switched off are skipped,
    so nobody is reminded about requests they have no screen to act on.
    """
    now = now or timezone.now()
    summary = {"warned": 0, "escalated": 0}
    enabled = {}  # tenant id -> adapter's answer, asked once per run
    for stage_id in _sla_candidate_ids():
        with transaction.atomic():
            stage = _lock_stage_if_active(stage_id)
            if stage is None or not _tenant_enabled(stage.request, enabled):
                continue
            outcome = _apply_sla(stage, now)
            if outcome:
                summary[outcome] += 1
    return summary


def _tenant_enabled(approval_request, answers):
    tenant_id = approval_request.tenant_id
    if tenant_id not in answers:
        answers[tenant_id] = get_adapter().is_enabled(approval_request.tenant)
    return answers[tenant_id]


def _sla_candidate_ids():
    """Started, unfinished stages of pending requests that still owe a reminder or an escalation."""
    still_due_an_action = Q(escalated_at__isnull=True) | Q(sla_warned_at__isnull=True)
    stages = ApprovalStage.objects.filter(
        request__status=RequestStatus.PENDING,
        completed_at__isnull=True,
        started_at__isnull=False,
        due_at__isnull=False,
    ).filter(still_due_an_action)
    return list(stages.values_list("id", flat=True))


def _lock_stage_if_active(stage_id):
    """Lock the stage, or return None if another run holds it or it is no longer the request's active stage."""
    stage = (
        ApprovalStage.objects.select_for_update(skip_locked=True, of=("self",))
        .select_related("request")
        .filter(pk=stage_id)
        .first()
    )
    if stage is None or stage.completed_at:
        return None
    request = stage.request
    if request.status != RequestStatus.PENDING or stage.order != request.current_stage:
        return None
    return stage


def _apply_sla(stage, now):
    """Escalate if the deadline has passed, otherwise remind once 75% of the window is used.
    Returns "escalated", "warned" or None."""
    if now >= stage.due_at:
        if stage.escalated_at:
            return None
        _escalate(stage, now)
        return "escalated"
    if stage.sla_warned_at or _elapsed_share(stage, now) < SlaState.WARNING_RATIO:
        return None
    _warn(stage, now)
    return "warned"


def _elapsed_share(stage, now):
    """How much of the stage's SLA window has been used, from 0.0 upwards."""
    window = (stage.due_at - stage.started_at).total_seconds() or 1
    return (now - stage.started_at).total_seconds() / window


def _warn(stage, now):
    stage.sla_warned_at = now
    stage.save(update_fields=["sla_warned_at"])
    percent = int(_elapsed_share(stage, now) * 100)
    _event(stage.request, EventType.SLA_WARNING, None, f"SLA reminder sent — {percent}% of allowed time elapsed.")
    transaction.on_commit(lambda: notify.sla_warning(stage.request))


def _escalate(stage, now):
    """Add the policy's escalation backup as an extra approver on the overdue stage (from the request's snapshot,
    so later policy edits don't change who it escalates to). With no backup configured, current checkers are
    reminded instead."""
    approval_request = stage.request
    user, role = parse_token(approval_request.policy_snapshot.get("escalation"))
    stage.escalated_at = now
    stage.save(update_fields=["escalated_at"])
    target = None
    if user or role:
        ApprovalStageApprover.objects.create(stage=stage, approver_user=user, approver_role=role, is_escalation=True)
        adapter = get_adapter()
        target = adapter.user_label(user) if user else adapter.role_label(role)
        message = f"SLA breached; escalated to {target}."
    else:
        message = "SLA breached; no escalation destination is configured, current approvers reminded."
    _event(approval_request, EventType.ESCALATED, None, message, data={"target": target})
    transaction.on_commit(lambda: notify.request_escalated(approval_request, target))
    _bump(approval_request.tenant_id)


# --- cache versioning (pending counts) ----------------------------------------------------------
# Pending counts are cached per user under a key that includes a per-tenant version number. Any change to the
# tenant's requests bumps the version, which invalidates every cached count for that tenant at once without having
# to know which users were affected.


def cache_version(tenant_id):
    return counts_cache().get(f"approvals:version:{tenant_id}", 0)


def _bump(tenant_id):
    cache = counts_cache()
    key = f"approvals:version:{tenant_id}"
    try:
        cache.incr(key)
    except ValueError:
        cache.set(key, 1, None)


# --- internals ----------------------------------------------------------------------------------


def _name(user):
    return get_adapter().user_label(user)


def _event(approval_request, event_type, actor, message, data=None):
    return ApprovalEvent.objects.create(
        request=approval_request, type=event_type, actor=actor, message=message, data=data or {}
    )


def _audit(approval_request, action, actor, before=None, after=None):
    audit.send(
        AuditRecord(
            action=action,
            resource_type="ApprovalRequest",
            resource_id=str(approval_request.pk),
            tenant_id=approval_request.tenant_id,
            actor_id=getattr(actor, "pk", None),
            before=before,
            after=after,
            metadata={"reference": approval_request.reference, "action_type": approval_request.action_type},
        )
    )


def _audit_delegation(delegation, actor, action):
    audit.send(
        AuditRecord(
            action=action,
            resource_type="ApprovalDelegation",
            resource_id=str(delegation.pk),
            tenant_id=delegation.tenant_id,
            actor_id=getattr(actor, "pk", None),
            after={
                "delegator": delegation.delegator_id,
                "delegate": delegation.delegate_id,
                "starts_at": delegation.starts_at.isoformat(),
                "ends_at": delegation.ends_at.isoformat(),
                "action_types": delegation.action_types,
                "revoked": bool(delegation.revoked_at),
            },
        )
    )
