"""Who is told about maker-checker events; delivery itself is the host's (``ApprovalsAdapter.notify``).

These functions run after the triggering transaction commits (``transaction.on_commit`` in ``services``), so
nobody is notified about a change that was rolled back. Each successful delivery is recorded as a "notified" event
on the request's timeline.
"""

import logging

from django.contrib.auth import get_user_model

from django_maker_checker.adapter import Notice
from django_maker_checker.conf import get_adapter
from django_maker_checker.constants import EventType, RequestStatus
from django_maker_checker.models import ApprovalDelegation, ApprovalEvent
from django_maker_checker.permissions import eligible_checkers
from django_maker_checker.registry import get_handler

logger = logging.getLogger(__name__)

REQUESTED = "requested"
DECIDED = "decided"
ESCALATED = "escalated"
CANCELLED = "cancelled"
SLA_WARNING = "sla_warning"


def current_checkers(approval_request):
    """Checkers on the active stage plus their current delegates (never the maker)."""
    stage = approval_request.active_stage
    if stage is None:
        return []
    checkers = eligible_checkers(
        approval_request.tenant,
        approval_request.action_type,
        list(stage.approvers.all()),
        exclude_user_ids=[approval_request.maker_id],
    )
    delegate_ids = _current_delegate_ids(approval_request, checkers) - {checker.pk for checker in checkers}
    delegates = get_user_model().objects.filter(pk__in=delegate_ids, is_active=True)
    return checkers + list(delegates)


def _current_delegate_ids(approval_request, delegators):
    """Ids of users currently standing in for any of ``delegators`` on this kind of action, except the maker."""
    delegations = ApprovalDelegation.objects.filter(
        tenant_id=approval_request.tenant_id, delegator__in=delegators, revoked_at__isnull=True
    )
    return {
        delegation.delegate_id
        for delegation in delegations
        if delegation.is_current
        and delegation.covers(approval_request.action_type)
        and delegation.delegate_id != approval_request.maker_id
    }


def subject(approval_request):
    """Short description used in notification texts, e.g. "Add User – Nina Analyst (REQ-2610-00001)"."""
    label = get_handler(approval_request.action_type).label
    target = f" – {approval_request.target_label}" if approval_request.target_label else ""
    return f"{label}{target} ({approval_request.reference})"


def _deliver(approval_request, kind, recipients, title, text, severity="info", record_as=None):
    """Hand one ``Notice`` to the host and log it on the timeline.

    Inactive users are dropped and nothing is sent to an empty audience. A failing host notifier is logged but never
    raised, since the decision it reports has already committed and must stand.
    """
    adapter = get_adapter()
    recipients = tuple(u for u in recipients if u and u.is_active)
    if not recipients:
        return
    notice = Notice(
        kind=kind,
        title=title,
        text=text,
        severity=severity,
        recipients=recipients,
        approval_request=approval_request,
        link=adapter.request_url(approval_request),
    )
    try:
        adapter.notify(notice)
    except Exception:  # noqa: BLE001 - delivery problems must not undo the decision
        logger.exception("Delivering %s notice for %s failed", kind, approval_request.reference)
        return
    if record_as:
        names = ", ".join(sorted({getattr(u, "email", "") or u.get_username() for u in recipients}))
        ApprovalEvent.objects.create(
            request=approval_request, type=EventType.NOTIFIED, message=f"{record_as} sent to {names}."
        )


def request_submitted(approval_request):
    """Tell the active stage's checkers (and their delegates) that a request awaits them. Also used when a request
    moves to its next stage."""
    approval_request.refresh_from_db()
    maker = get_adapter().user_label(approval_request.maker)
    _deliver(
        approval_request,
        REQUESTED,
        current_checkers(approval_request),
        "Approval required",
        f"{maker} requested approval for {subject(approval_request)}.",
        record_as="Approval notification",
    )


def request_decided(approval_request):
    """Tell the maker the outcome: rejected (with the reason), applied, or approved but failed to apply."""
    approval_request.refresh_from_db()
    status = approval_request.status
    if status == RequestStatus.REJECTED:
        title, severity = "Request rejected", "warning"
        text = f"{subject(approval_request)} was rejected. Reason: {approval_request.rejection_reason}"
    elif status == RequestStatus.EXECUTED:
        title, severity = "Request approved", "info"
        text = f"{subject(approval_request)} was approved and applied."
    elif status == RequestStatus.FAILED:
        title, severity = "Approved change failed", "error"
        text = f"{subject(approval_request)} was approved but applying it failed: {approval_request.execution_error}"
    else:
        return
    _deliver(
        approval_request, DECIDED, [approval_request.maker], title, text, severity, record_as="Outcome notification"
    )


def request_cancelled(approval_request, by_maker=True):
    """Tell the checkers who were assigned that it no longer needs action, and the maker if someone else cancelled.

    The request is no longer pending, so the stage is looked up by ``current_stage`` instead of ``active_stage``.
    """
    users = [] if by_maker else [approval_request.maker]
    stage = approval_request.stages.filter(order=approval_request.current_stage).first()
    if stage:
        users += eligible_checkers(
            approval_request.tenant,
            approval_request.action_type,
            list(stage.approvers.all()),
            exclude_user_ids=[approval_request.maker_id],
        )
    _deliver(
        approval_request,
        CANCELLED,
        users,
        "Approval request cancelled",
        f"{subject(approval_request)} was cancelled and no longer needs action.",
        record_as="Cancellation notice",
    )


def request_escalated(approval_request, target):
    """Tell the current checkers, including the escalation backup just added, that the SLA passed."""
    text = f"{subject(approval_request)} passed its SLA" + (f" and was escalated to {target}." if target else ".")
    _deliver(
        approval_request,
        ESCALATED,
        current_checkers(approval_request),
        "Approval escalated",
        text,
        "warning",
        record_as="Escalation notice",
    )


def sla_warning(approval_request):
    """Remind the current checkers when a stage reaches 75% of its SLA window."""
    _deliver(
        approval_request,
        SLA_WARNING,
        current_checkers(approval_request),
        "Approval due soon",
        f"{subject(approval_request)} is close to its SLA deadline.",
        "warning",
        record_as="SLA reminder",
    )
