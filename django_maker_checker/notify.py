"""Who is told about maker-checker events; delivery itself is the host's (``ApprovalsAdapter.notify``).

These functions run after the triggering transaction commits (``transaction.on_commit`` in ``services``), so
nobody is notified about a change that was rolled back. Each successful delivery is recorded as a "notified" event
on the request's timeline.
"""

import logging

from django.contrib.auth import get_user_model
from django.utils.translation import gettext as _

from django_maker_checker.adapter import Notice
from django_maker_checker.conf import get_adapter
from django_maker_checker.constants import EventType, RequestStatus
from django_maker_checker.models import ApprovalDelegation, ApprovalEvent, same_db
from django_maker_checker.permissions import eligible_checkers
from django_maker_checker.registry import get_handler

logger = logging.getLogger(__name__)

REQUESTED = "requested"
DECIDED = "decided"
ESCALATED = "escalated"
CANCELLED = "cancelled"
SLA_WARNING = "sla_warning"
CHANGES_REQUESTED = "changes_requested"


def current_checkers(approval_request):
    """Checkers on the active stage plus their current delegates (never the maker)."""
    stage = approval_request.active_stage
    if stage is None:
        return []
    checkers = eligible_checkers(
        approval_request.tenant,
        approval_request.action_type,
        list(same_db(stage, stage.approvers).all()),
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
    values = {"action": label, "target": approval_request.target_label, "reference": approval_request.reference}
    if approval_request.target_label:
        return _("%(action)s – %(target)s (%(reference)s)") % values
    return _("%(action)s (%(reference)s)") % values


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
            request=approval_request,
            type=EventType.NOTIFIED,
            message=_("%(notice)s sent to %(names)s.") % {"notice": record_as, "names": names},
        )


def request_submitted(approval_request):
    """Tell the active stage's checkers (and their delegates) that a request awaits them. Also used when a request
    moves to its next stage."""
    approval_request.refresh_from_db(using=approval_request._state.db)
    maker = get_adapter().user_label(approval_request.maker)
    _deliver(
        approval_request,
        REQUESTED,
        current_checkers(approval_request),
        _("Approval required"),
        _("%(maker)s requested approval for %(subject)s.") % {"maker": maker, "subject": subject(approval_request)},
        record_as=_("Approval notification"),
    )


def approver_assigned(approval_request, slot):
    """Tell whoever now fills ``slot`` (after ``services.reassign_approver``) that the request awaits them."""
    _deliver(
        approval_request,
        REQUESTED,
        eligible_checkers(
            approval_request.tenant, approval_request.action_type, [slot], exclude_user_ids=[approval_request.maker_id]
        ),
        _("Approval required"),
        _("%(subject)s was reassigned to you for approval.") % {"subject": subject(approval_request)},
        record_as=_("Reassignment notice"),
    )


def request_decided(approval_request):
    """Tell the maker the outcome: rejected (with the reason), applied, or approved but failed to apply."""
    approval_request.refresh_from_db(using=approval_request._state.db)
    status = approval_request.status
    values = {
        "subject": subject(approval_request),
        "reason": approval_request.rejection_reason,
        "error": approval_request.execution_error,
    }
    if status == RequestStatus.REJECTED:
        title, severity = _("Request rejected"), "warning"
        text = _("%(subject)s was rejected. Reason: %(reason)s") % values
    elif status == RequestStatus.EXECUTED:
        title, severity = _("Request approved"), "info"
        text = _("%(subject)s was approved and applied.") % values
    elif status == RequestStatus.FAILED:
        title, severity = _("Approved change failed"), "error"
        text = _("%(subject)s was approved but applying it failed: %(error)s") % values
    else:
        return
    _deliver(
        approval_request, DECIDED, [approval_request.maker], title, text, severity, record_as=_("Outcome notification")
    )


def changes_requested(approval_request, note):
    """Tell the maker their request was sent back, and what to change."""
    _deliver(
        approval_request,
        CHANGES_REQUESTED,
        [approval_request.maker],
        _("Changes requested"),
        _("%(subject)s was sent back for changes: %(note)s") % {"subject": subject(approval_request), "note": note},
        severity="warning",
        record_as=_("Changes-requested notice"),
    )


def request_cancelled(approval_request, by_maker=True):
    """Tell the checkers who were assigned that it no longer needs action, and the maker if someone else cancelled.

    The request is no longer pending, so the stage is looked up by ``current_stage`` instead of ``active_stage``.
    """
    users = [] if by_maker else [approval_request.maker]
    stage = same_db(approval_request, approval_request.stages).filter(order=approval_request.current_stage).first()
    if stage:
        users += eligible_checkers(
            approval_request.tenant,
            approval_request.action_type,
            list(same_db(stage, stage.approvers).all()),
            exclude_user_ids=[approval_request.maker_id],
        )
    _deliver(
        approval_request,
        CANCELLED,
        users,
        _("Approval request cancelled"),
        _("%(subject)s was cancelled and no longer needs action.") % {"subject": subject(approval_request)},
        record_as=_("Cancellation notice"),
    )


def request_escalated(approval_request, target):
    """Tell the current checkers, including the escalation backup just added, that the SLA passed."""
    values = {"subject": subject(approval_request), "target": target}
    if target:
        text = _("%(subject)s passed its SLA and was escalated to %(target)s.") % values
    else:
        text = _("%(subject)s passed its SLA.") % values
    _deliver(
        approval_request,
        ESCALATED,
        current_checkers(approval_request),
        _("Approval escalated"),
        text,
        "warning",
        record_as=_("Escalation notice"),
    )


def sla_warning(approval_request):
    """Remind the current checkers when a stage reaches 75% of its SLA window."""
    _deliver(
        approval_request,
        SLA_WARNING,
        current_checkers(approval_request),
        _("Approval due soon"),
        _("%(subject)s is close to its SLA deadline.") % {"subject": subject(approval_request)},
        "warning",
        record_as=_("SLA reminder"),
    )
