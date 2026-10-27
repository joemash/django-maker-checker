"""All writes to maker-checker state go through this module.

Hosts call ``submit_or_execute`` / ``decide`` / ``cancel`` and never touch ``ApprovalRequest.status`` directly.
Every operation that changes a request locks its row (``select_for_update``) so concurrent approvals can't execute
a change twice; notifications run on commit.
"""

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, router, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from django_maker_checker import audit, notify
from django_maker_checker.adapter import AuditRecord
from django_maker_checker.conf import comment_max_length, counts_cache, get_adapter, role_model_name
from django_maker_checker.constants import Decision, EventType, RequestStatus, SlaState, WorkflowType
from django_maker_checker.exceptions import ApprovalError
from django_maker_checker.models import (
    ApprovalDecision,
    ApprovalDelegation,
    ApprovalEvent,
    ApprovalLock,
    ApprovalPolicy,
    ApprovalRequest,
    ApprovalStage,
    ApprovalStageApprover,
    required_approvals_count,
    same_db,
)
from django_maker_checker.permissions import (
    can_cancel,
    can_resubmit,
    can_retry,
    can_view,
    decided_this_round,
    decision_slot,
    eligible_checkers,
    has_approve_permission,
    is_member_of,
)
from django_maker_checker.policies import current_version
from django_maker_checker.registry import ExecutionContext, Scope, all_handlers, get_handler, to_payload

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
    kind, _separator, raw_id = (token or "").partition(":")
    if not raw_id.isdigit():
        return None, None
    if kind == "u":
        return get_user_model().objects.filter(pk=int(raw_id)).first(), None
    if kind == "r":
        return None, apps.get_model(role_model_name()).objects.filter(pk=int(raw_id)).first()
    return None, None


@dataclass(frozen=True)
class _StagePlan:
    """One stage to create: its approver slots and how many of them must approve."""

    slots: list
    require_all: bool = False
    required_approvals: Optional[int] = None

    @property
    def required(self):
        return required_approvals_count(len(self.slots), self.require_all, self.required_approvals)


def _stage_layout(policy):
    """The stages a request under ``policy`` gets.

    SINGLE uses the first slot only; MULTI puts every slot in one stage (any one, N of them, or all); SEQUENTIAL makes
    one stage per slot, in order.
    """
    approvers = policy.ordered_approvers()
    if policy.workflow_type == WorkflowType.SEQUENTIAL:
        return [_StagePlan([a]) for a in approvers]
    if policy.workflow_type == WorkflowType.MULTI:
        return [_StagePlan(approvers, policy.require_all, policy.required_approvals)]
    return [_StagePlan(approvers[:1])]


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
        raise ApprovalError(_("Approvals are switched off for this organisation."))
    policy = policy or find_policy(tenant, action_type, payload)
    if policy is None:
        raise ApprovalError(_("No approval policy applies to this action."))

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
            _take_locks(approval_request, handler.lock_keys(payload))
            _event(approval_request, EventType.CREATED, maker, _submitted_message(maker, handler, approval_request))
            transaction.on_commit(lambda: notify.request_submitted(approval_request))
    except IntegrityError:
        raise _already_open_error(tenant, action_type, lock_key) from None

    _bump(tenant.pk)
    _audit(approval_request, "created", maker, after={"status": approval_request.status})
    return approval_request


def _ensure_every_stage_has_a_checker(tenant, action_type, layout, maker):
    """Each stage needs as many eligible people other than the maker as it needs approvals, otherwise the request
    could never complete. For the same reason no user may be named on two stages: one person acts once per round
    (rules saved before ``save_policy`` refused this can still name someone twice)."""
    if len(layout) > 1:
        named = [slot.approver_user_id for plan in layout for slot in plan.slots if slot.approver_user_id]
        if len(named) != len(set(named)):
            raise ApprovalError(
                _(
                    "The same person is named on more than one step of this approval flow, so it could never be "
                    "completed. Ask an administrator to update the approval configuration."
                )
            )
    for number, plan in enumerate(layout, start=1):
        eligible = len(eligible_checkers(tenant, action_type, plan.slots, exclude_user_ids=[maker.id]))
        if eligible >= plan.required:
            continue
        problem = _missing_checkers_message(number if len(layout) > 1 else None, plan.required, eligible)
        raise ApprovalError(
            _("%(problem)s Ask an administrator to update the approval configuration.") % {"problem": problem}
        )


def _missing_checkers_message(step, required, eligible):
    """Why a stage can't be approved; ``step`` is None for a single-stage request."""
    if eligible == 0:
        if step is None:
            return _("No eligible approver other than you is configured.")
        return _("No eligible approver other than you is configured for step %(step)s.") % {"step": step}
    values = {"required": required, "eligible": eligible, "step": step}
    if step is None:
        return (
            ngettext(
                "%(required)s approvals are needed, but only %(eligible)s eligible approver other than you "
                "is configured.",
                "%(required)s approvals are needed, but only %(eligible)s eligible approvers other than you "
                "are configured.",
                eligible,
            )
            % values
        )
    return (
        ngettext(
            "%(required)s approvals are needed for step %(step)s, but only %(eligible)s eligible approver "
            "other than you is configured.",
            "%(required)s approvals are needed for step %(step)s, but only %(eligible)s eligible approvers "
            "other than you are configured.",
            eligible,
        )
        % values
    )


def _submitted_message(maker, handler, approval_request):
    values = {"maker": _name(maker), "action": handler.label, "target": approval_request.target_label}
    if approval_request.target_label:
        return _("%(maker)s submitted %(action)s for %(target)s.") % values
    return _("%(maker)s submitted %(action)s.") % values


def _create_stages(approval_request, layout, now, sla_minutes):
    """One stage per layout entry, copying the policy's approver slots. Only the first stage starts (and gets an SLA
    deadline) now; later ones start when the previous stage completes."""
    for order, plan in enumerate(layout):
        first = order == 0
        stage = ApprovalStage.objects.create(
            request=approval_request,
            order=order,
            require_all=plan.require_all,
            required_approvals=plan.required_approvals,
            started_at=now if first else None,
            due_at=_sla_due(now, sla_minutes) if first else None,
        )
        ApprovalStageApprover.objects.bulk_create(
            ApprovalStageApprover(stage=stage, approver_user_id=s.approver_user_id, approver_role_id=s.approver_role_id)
            for s in plan.slots
        )


def _take_locks(approval_request, keys):
    """Claim the items in ``keys`` for the request (``ActionHandler.lock_keys``). An item another open request
    already holds is refused with an error naming that request; the savepoint keeps the caller's transaction usable."""
    keys = list(dict.fromkeys(keys))
    if not keys:
        return
    try:
        with transaction.atomic():
            ApprovalLock.objects.bulk_create(
                ApprovalLock(tenant_id=approval_request.tenant_id, key=key, request=approval_request) for key in keys
            )
    except IntegrityError:
        raise _items_held_error(approval_request, keys) from None


def _release_locks(approval_request):
    """Free the request's items once it is no longer open."""
    ApprovalLock.objects.filter(request=approval_request).delete()


def _items_held_error(approval_request, keys):
    holder = (
        _primary(ApprovalLock)
        .filter(tenant_id=approval_request.tenant_id, key__in=keys)
        .exclude(request=approval_request)
        .select_related("request")
        .first()
    )
    if holder:
        return ApprovalError(
            _("Another open approval request (%(reference)s) already covers some of these items.")
            % {"reference": holder.request.reference}
        )
    return ApprovalError(_("Another open approval request already covers some of these items."))


def _already_open_error(tenant, action_type, lock_key):
    """The error for a duplicate submission, naming the open request that blocks it."""
    existing = (
        _primary(ApprovalRequest)
        .filter(tenant=tenant, action_type=action_type, lock_key=lock_key, status__in=RequestStatus.OPEN)
        .first()
    )
    if existing:
        return ApprovalError(
            _("There is already an open approval request (%(reference)s) for this item.")
            % {"reference": existing.reference}
        )
    return ApprovalError(_("There is already an open approval request for this item."))


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
    if approval_request.status == RequestStatus.CHANGES_REQUESTED:
        return _("This request was sent back to its maker for changes.")
    if approval_request.status != RequestStatus.PENDING:
        return _("This request is already %(status)s.") % {"status": approval_request.get_status_display().lower()}
    if user.id == approval_request.maker_id:
        return _("You cannot approve or reject a request you raised.")
    if decided_this_round(user, approval_request):
        return _("You have already acted on this request.")
    return _("You are not an approver for the current step of this request.")


def decide(request_id, user, decision, comment="", http_request=None):
    """Record ``user``'s decision on the request's current stage and move the request on.

    The request row is locked for the whole decision, so concurrent checkers are serialised and each sees the
    other's effect. A rejection ends the request. Requesting changes sends it back to the maker, who can amend and
    ``resubmit`` it (or cancel it). An approval fills the user's slot. When that completes the stage,
    the request either moves to the next stage (which starts its own SLA window, and its checkers are notified) or,
    on the last stage, becomes APPROVED. The change is then applied by ``execute_approved`` *after* the decision
    commits, so a failure to apply can never undo the approval.
    """
    comment = _checked_comment(decision, comment)

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
            submission=approval_request.submission,
        )
        fully_approved = False
        if decision == Decision.REJECT:
            _reject(approval_request, stage, slot, user, comment)
        elif decision == Decision.REQUEST_CHANGES:
            _send_back(approval_request, slot, user, comment)
        else:
            fully_approved = _approve(approval_request, stage, slot, user, comment)

    _bump(approval_request.tenant_id)
    _audit(approval_request, "updated", user, after={"status": approval_request.status, "decision": decision})
    if fully_approved:
        approval_request = execute_approved(approval_request.pk, user, http_request)
        notify.request_decided(approval_request)
    return approval_request


def _checked_comment(decision, comment):
    """The decision's comment, stripped; refuses unknown decisions, comments over the length limit, and rejections or
    change requests without one."""
    if decision not in dict(Decision.CHOICES):
        raise ApprovalError(_("Unknown decision."))
    comment = _checked_length(comment)
    if decision == Decision.REJECT and not comment:
        raise ApprovalError(_("A reason is required to reject a request."))
    if decision == Decision.REQUEST_CHANGES and not comment:
        raise ApprovalError(_("Say what needs to change."))
    return comment


def _checked_length(text):
    """``text`` stripped, refused when longer than ``APPROVALS_COMMENT_MAX_LENGTH``."""
    text = (text or "").strip()
    limit = comment_max_length()
    if len(text) > limit:
        raise ApprovalError(_("This note is too long: use at most %(limit)s characters.") % {"limit": limit})
    return text


def _who(user, slot):
    """Who acted, for timeline messages: "Chris", or "Chris on behalf of Carol" when delegated."""
    if slot.on_behalf_of:
        return _("%(actor)s on behalf of %(delegator)s") % {"actor": _name(user), "delegator": _name(slot.on_behalf_of)}
    return _name(user)


def _reject(approval_request, stage, slot, user, comment):
    """End the request as rejected; the maker is told the reason after commit."""
    try:
        approval_request.reject(by=user, reason=comment)
    except ValidationError as err:
        raise ApprovalError(" ".join(err.messages)) from None
    approval_request.save()
    _release_locks(approval_request)
    stage.completed_at = timezone.now()
    stage.save(update_fields=["completed_at"])
    message = _("Rejected by %(who)s. Reason: %(reason)s") % {"who": _who(user, slot), "reason": comment}
    _event(approval_request, EventType.REJECTED, user, message)
    transaction.on_commit(lambda: notify.request_decided(approval_request))


def _send_back(approval_request, slot, user, note):
    """Return the request to its maker. It keeps its lock on the item, and its SLA pauses until it is resubmitted."""
    approval_request.request_changes(note=note)
    approval_request.save()
    message = _("Changes requested by %(who)s: %(note)s") % {"who": _who(user, slot), "note": note}
    _event(approval_request, EventType.CHANGES_REQUESTED, user, message)
    transaction.on_commit(lambda: notify.changes_requested(approval_request, note))


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
        message = _("Approved by %(who)s (%(done)s of %(total)s required approvals).") % {
            "who": _who(user, slot),
            "done": done,
            "total": total,
        }
        _event(approval_request, EventType.STAGE_APPROVED, user, message)
        return False

    stage.completed_at = now
    stage.save(update_fields=["completed_at"])
    next_stage = same_db(approval_request, approval_request.stages).filter(order=stage.order + 1).first()
    if next_stage is not None:
        _start_stage(approval_request, next_stage, now)
        message = _("Step %(step)s approved by %(who)s; routed to step %(next)s.") % {
            "step": stage.order + 1,
            "who": _who(user, slot),
            "next": next_stage.order + 1,
        }
        _event(approval_request, EventType.STAGE_APPROVED, user, message)
        transaction.on_commit(lambda: notify.request_submitted(approval_request))
        return False

    approval_request.approve(by=user)
    approval_request.save()
    _event(approval_request, EventType.APPROVED, user, _("Approved by %(who)s.") % {"who": _who(user, slot)})
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
    regular = [a for a in same_db(stage, stage.approvers).all() if not a.is_escalation]
    return sum(1 for a in regular if a.satisfied_at), stage.required_count(regular)


def _stage_complete(stage, approver):
    """A stage completes once it has the approvals it requires (one, N of M, or all). An escalation approver's
    approval completes it regardless, since escalation exists to unblock a stalled step."""
    if approver.is_escalation:
        return True
    done, required = stage_progress_counts(stage)
    return done >= required


def execute_approved(request_id, actor, http_request=None):
    """Apply an approved change. A failure leaves the decision intact and marks the request failed.

    The handler's ``validate`` runs first, inside the same locked transaction as ``execute``, so the change is
    re-checked against the current state of the item immediately before it is applied. On any exception the
    transaction rolls back and a fresh one records FAILED with the error; ``ApprovalError`` messages are expected
    and aren't logged as crashes.
    """
    approval_request = _primary(ApprovalRequest).get(pk=request_id)
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
            _release_locks(approval_request)
            _event(approval_request, EventType.EXECUTED, None, _("Change applied."))
    except Exception as exc:  # noqa: BLE001 - any failure must be recorded, not lost
        if not isinstance(exc, ApprovalError):
            logger.exception("Executing approval request %s failed", request_id)
        with transaction.atomic():
            approval_request = ApprovalRequest.objects.select_for_update().get(pk=request_id)
            if approval_request.status == RequestStatus.APPROVED:
                approval_request.mark_failed(exc)
                approval_request.save()
                _release_locks(approval_request)
                _event(
                    approval_request,
                    EventType.FAILED,
                    None,
                    _("Applying the change failed: %(error)s") % {"error": exc},
                )
    _bump(approval_request.tenant_id)
    _audit(approval_request, "updated", actor, after={"status": approval_request.status})
    return approval_request


def retry(request_id, user, http_request=None):
    """Re-apply a FAILED request (administrators only); the original approval stands, so no new decision is needed."""
    with transaction.atomic():
        approval_request = ApprovalRequest.objects.select_for_update().get(pk=request_id)
        if not can_retry(user, approval_request):
            raise ApprovalError(_("Only approval administrators can retry a failed request."))
        approval_request.retry()
        try:
            # Retrying reopens the request, so its item must not have been claimed by another request meanwhile.
            with transaction.atomic():
                approval_request.save()
        except IntegrityError:
            raise _already_open_error(
                approval_request.tenant, approval_request.action_type, approval_request.lock_key
            ) from None
        _take_locks(approval_request, get_handler(approval_request.action_type).lock_keys(approval_request.payload))
        _event(
            approval_request,
            EventType.RETRIED,
            user,
            _("%(name)s retried applying the change.") % {"name": _name(user)},
        )
    return execute_approved(request_id, user, http_request)


def cancel(request_id, user, reason=""):
    """Withdraw a pending request: by its maker, or by an administrator (the maker is then told who cancelled it)."""
    reason = _checked_length(reason)
    with transaction.atomic():
        approval_request = ApprovalRequest.objects.select_for_update().get(pk=request_id)
        if not can_cancel(user, approval_request):
            raise ApprovalError(_("You cannot cancel this request."))
        approval_request.cancel(by=user)
        approval_request.save()
        _release_locks(approval_request)
        by_maker = user.id == approval_request.maker_id
        message = _cancelled_message(user, by_maker, reason)
        _event(approval_request, EventType.CANCELLED, user, message)
        transaction.on_commit(lambda: notify.request_cancelled(approval_request, by_maker=by_maker))
    _bump(approval_request.tenant_id)
    _audit(approval_request, "updated", user, after={"status": approval_request.status})
    return approval_request


def _cancelled_message(user, by_maker, reason):
    message = _("Cancelled by the maker.") if by_maker else _("Cancelled by %(name)s.") % {"name": _name(user)}
    if reason:
        message += " " + _("Reason: %(reason)s") % {"reason": reason}
    return message


def resubmit(request_id, user, payload=None, justification=None):
    """Send a request that was returned for changes back for approval, as a new round.

    Only the maker resubmits. ``payload`` replaces the proposed change (it must still be for the same item, i.e. have
    the same lock key); ``justification`` replaces the maker's justification. The item's current state is snapshotted
    again, and approval restarts from the first stage with a fresh SLA: approvals from the earlier round are
    discarded, because they approved a different change.
    """
    with transaction.atomic():
        approval_request = ApprovalRequest.objects.select_for_update().get(pk=request_id)
        if not can_resubmit(user, approval_request):
            raise ApprovalError(_("Only the maker can resubmit a request that was sent back for changes."))
        handler = get_handler(approval_request.action_type)
        changed = payload is not None
        if changed:
            _replace_payload(approval_request, handler, to_payload(payload))
        if justification is not None:
            approval_request.justification = justification.strip()
        approval_request.before_snapshot = handler.snapshot_before(approval_request.tenant, approval_request.payload)
        _ensure_every_stage_has_a_checker(
            approval_request.tenant, approval_request.action_type, _plans_of(approval_request), user
        )
        approval_request.resubmit()
        approval_request.save()
        _restart_stages(approval_request, timezone.now())
        values = {"name": _name(user)}
        if changed:
            message = _("%(name)s resubmitted the request with changes.") % values
        else:
            message = _("%(name)s resubmitted the request.") % values
        _event(approval_request, EventType.RESUBMITTED, user, message)
        transaction.on_commit(lambda: notify.request_submitted(approval_request))
    _bump(approval_request.tenant_id)
    _audit(approval_request, "updated", user, after={"status": approval_request.status})
    return approval_request


def _replace_payload(approval_request, handler, payload):
    if handler.lock_key(payload) != approval_request.lock_key:
        raise ApprovalError(_("A resubmitted request must still change the same item."))
    approval_request.payload = payload
    approval_request.target_label = handler.target_label(payload)[:255]
    _release_locks(approval_request)
    _take_locks(approval_request, handler.lock_keys(payload))


def _plans_of(approval_request):
    """The request's stages as plans, for checking that each can still be approved."""
    plans = []
    for stage in same_db(approval_request, approval_request.stages).all():
        regular = [a for a in same_db(stage, stage.approvers).all() if not a.is_escalation]
        plans.append(_StagePlan(regular, stage.require_all, stage.required_approvals))
    return plans


def _restart_stages(approval_request, now):
    """Clear every stage's progress (and approvers added by escalation), then start the first stage."""
    ApprovalStageApprover.objects.filter(stage__request=approval_request, is_escalation=True).delete()
    ApprovalStageApprover.objects.filter(stage__request=approval_request).update(satisfied_at=None, satisfied_by=None)
    approval_request.stages.update(
        started_at=None, due_at=None, sla_warned_at=None, escalated_at=None, completed_at=None
    )
    _start_stage(approval_request, same_db(approval_request, approval_request.stages).get(order=0), now)


def add_comment(request_id, user, text):
    """Add a note to the request's timeline; anyone who may view the request may comment."""
    text = _checked_length(text)
    if not text:
        raise ApprovalError(_("Comment cannot be empty."))
    approval_request = ApprovalRequest.objects.get(pk=request_id)
    if not can_view(user, approval_request):
        raise ApprovalError(_("You cannot comment on this request."))
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
    _event(approval_request, EventType.VIEWED, user, _("Opened by %(name)s.") % {"name": _name(user)})


# --- reassignment -------------------------------------------------------------------------------


def reassign_approver(user, *, by=None):
    """Hand ``user``'s unfilled approver slots on open requests to someone who can still act on them.

    Call it when ``user`` can no longer approve, e.g. from the host's deactivate or remove-user flow once they have
    lost access. Requests keep the approvers they were raised with, so without this a step naming someone who left
    could never be approved.

    Each unfilled slot naming ``user`` directly, on a stage that hasn't finished, goes to the approver the rule names
    in the same position now, or else to the request's escalation backup. Role slots are left alone: the role's other
    members still fill them. A replacement must be able to approve, must not be the maker, and must not already be
    named on the request, since one person acts once per round. A slot with no such replacement is left as it is and
    the timeline says so, for an administrator to cancel the request or fix the rule. Whoever takes over a slot on the
    active stage is notified on commit.

    Returns ``{"reassigned": n, "unassigned": n}``.
    """
    summary = {"reassigned": 0, "unassigned": 0}
    slots = _primary(ApprovalStageApprover).filter(
        approver_user=user,
        is_escalation=False,
        satisfied_at__isnull=True,
        stage__completed_at__isnull=True,
        stage__request__status__in=RequestStatus.CANCELLABLE,
    )
    for request_id, slot_id in list(slots.values_list("stage__request_id", "id")):
        with transaction.atomic():
            approval_request = ApprovalRequest.objects.select_for_update().get(pk=request_id)
            slot = slots.select_related("stage").filter(pk=slot_id).first()  # re-checked now the request is locked
            if slot is None or approval_request.status not in RequestStatus.CANCELLABLE:
                continue
            outcome = _reassign_slot(approval_request, slot, user, by)
        summary[outcome] += 1
        _bump(approval_request.tenant_id)
        _audit(approval_request, "updated", by, after={"reassigned_from": user.pk, "outcome": outcome})
    return summary


def _reassign_slot(approval_request, slot, leaving, by):
    """Give ``slot`` to its replacement, or log that there is none. Returns "reassigned" or "unassigned"."""
    values = {"name": _name(leaving), "step": slot.stage.order + 1}
    replacement = _replacement_for(approval_request, slot, leaving)
    if replacement is None:
        message = _(
            "%(name)s can no longer approve step %(step)s and no replacement approver is configured: cancel the "
            "request or update the rule."
        )
        _event(approval_request, EventType.REASSIGNED, by, message % values)
        return "unassigned"
    slot.approver_user_id, slot.approver_role_id = replacement.approver_user_id, replacement.approver_role_id
    slot.save(update_fields=["approver_user", "approver_role"])
    values["target"] = replacement.approver_label
    message = _("%(name)s can no longer approve step %(step)s; reassigned to %(target)s.") % values
    _event(approval_request, EventType.REASSIGNED, by, message)
    if approval_request.status == RequestStatus.PENDING and slot.stage.order == approval_request.current_stage:
        transaction.on_commit(lambda: notify.approver_assigned(approval_request, slot))
    return "reassigned"


def _replacement_for(approval_request, slot, leaving):
    """The approver to take over ``slot``: the rule's approver at the same position now, else the request's
    escalation backup, whichever can act first (see ``reassign_approver``); None when neither can."""
    others = _primary(ApprovalStageApprover).filter(stage__request=approval_request).exclude(pk=slot.pk)
    excluded = {leaving.pk, approval_request.maker_id}
    excluded |= {user_id for user_id in others.values_list("approver_user_id", flat=True) if user_id}
    for candidate in (_rule_approver_at(approval_request, slot), _escalation_backup(approval_request)):
        if candidate is None or candidate.approver_user_id in excluded:
            continue
        exclude_user_ids = [leaving.pk, approval_request.maker_id]
        if eligible_checkers(approval_request.tenant, approval_request.action_type, [candidate], exclude_user_ids):
            return candidate
    return None


def _rule_approver_at(approval_request, slot):
    """The approver the request's rule now names at ``slot``'s position, while the rule keeps the workflow the
    request was raised under (positions mean different things in different workflows)."""
    policy = approval_request.policy
    if policy is None or policy.workflow_type != approval_request.policy_snapshot.get("workflow_type"):
        return None
    regular = _primary(ApprovalStageApprover).filter(stage__request=approval_request, is_escalation=False)
    position = (
        regular.filter(stage__order__lt=slot.stage.order).count()
        + regular.filter(stage=slot.stage, id__lt=slot.id).count()
    )
    approvers = policy.ordered_approvers()
    return approvers[position] if position < len(approvers) else None


def _escalation_backup(approval_request):
    """The escalation backup from the request's snapshot, as an unsaved slot, or None."""
    user, role = parse_token(approval_request.policy_snapshot.get("escalation"))
    if user is None and role is None:
        return None
    return ApprovalStageApprover(approver_user=user, approver_role=role)


# --- delegation ---------------------------------------------------------------------------------


def delegate(*, delegator, delegate_user, starts_at, ends_at, action_types=(), reason="", created_by=None):
    """Create a delegation after checking the delegator may approve and both users share a tenant."""
    tenant = get_adapter().tenant_of(delegator)
    if tenant is None:
        raise ApprovalError(_("You are not a member of a tenant."))
    if not any(has_approve_permission(delegator, t) for t in action_types or _all_action_types()):
        raise ApprovalError(_("Only approvers can delegate approvals."))
    if not is_member_of(delegate_user, tenant.pk):
        raise ApprovalError(_("The delegate must be an active member of your organisation."))
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
        raise ApprovalError(_("You cannot end this delegation."))
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
    stages = (
        _primary(ApprovalStage)
        .filter(
            request__status=RequestStatus.PENDING,
            completed_at__isnull=True,
            started_at__isnull=False,
            due_at__isnull=False,
        )
        .filter(still_due_an_action)
    )
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
    _event(
        stage.request,
        EventType.SLA_WARNING,
        None,
        _("SLA reminder sent — %(percent)s%% of allowed time elapsed.") % {"percent": percent},
    )
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
        message = _("SLA breached; escalated to %(target)s.") % {"target": target}
    else:
        message = _("SLA breached; no escalation destination is configured, current approvers reminded.")
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


def _primary(model):
    """``model``'s rows on the database writes go to, for reading rows that may only just have been written (which a
    replica may not have yet)."""
    return model._default_manager.db_manager(router.db_for_write(model))


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
