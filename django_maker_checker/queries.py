"""Read side of the engine: queues, filters, SLA status, stage progress and what a user may do.

Host UIs render these results; they should not re-derive any of it from the models.
"""

from dataclasses import dataclass
from datetime import date
from typing import Optional

from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from django_maker_checker.conf import cache_timeout, counts_cache, get_adapter
from django_maker_checker.constants import RequestStatus, SlaState
from django_maker_checker.models import ApprovalLock, ApprovalPolicyRevision, ApprovalRequest
from django_maker_checker.permissions import (
    can_cancel,
    can_decide,
    can_manage_policies,
    can_resubmit,
    can_retry,
    decision_slot,
    has_approve_permission,
    tenant_of,
)
from django_maker_checker.policies import current_version
from django_maker_checker.registry import all_handlers, get_handler
from django_maker_checker.services import cache_version

# --- filtering ----------------------------------------------------------------------------------


@dataclass
class RequestFilter:
    """Filters for request lists. Empty values mean "any". ``RequestFilter()`` (all defaults) matches everything, so a
    UI can compare against it to tell whether any filter is active."""

    text: str = ""  # reference, target, action label, maker name/email
    status: str = ""
    action_type: str = ""
    risk_level: str = ""
    maker_id: Optional[int] = None
    submitted_from: Optional[date] = None
    submitted_to: Optional[date] = None

    def matches(self, approval_request) -> bool:
        """Applied in Python, after the permission-filtered queues are built (see ``awaiting``). Dates compare in the
        local timezone, matching what users see. ``text`` is a case-insensitive substring of reference, target,
        action label or the maker's name, email or username."""
        return (
            self._fields_match(approval_request)
            and self._submitted_in_range(approval_request)
            and self._text_matches(approval_request)
        )

    def _fields_match(self, approval_request) -> bool:
        """Every exact-match filter that is set equals the request's value."""
        wanted = {
            "status": self.status,
            "action_type": self.action_type,
            "risk_level": self.risk_level,
            "maker_id": self.maker_id,
        }
        return all(getattr(approval_request, field) == value for field, value in wanted.items() if value)

    def _submitted_in_range(self, approval_request) -> bool:
        submitted = timezone.localtime(approval_request.submitted_at).date()
        if self.submitted_from and submitted < self.submitted_from:
            return False
        if self.submitted_to and submitted > self.submitted_to:
            return False
        return True

    def _text_matches(self, approval_request) -> bool:
        if not self.text:
            return True
        maker = approval_request.maker
        searchable = [
            approval_request.reference or "",
            approval_request.target_label,
            action_label(approval_request.action_type),
            get_adapter().user_label(maker),
            getattr(maker, "email", "") or "",
            maker.get_username(),
        ]
        return self.text.lower() in " ".join(searchable).lower()


def action_label(action_type):
    try:
        return get_handler(action_type).label
    except LookupError:
        return action_type


# --- queues -------------------------------------------------------------------------------------


def approvable_action_types(user):
    return [h.action_type for h in all_handlers() if has_approve_permission(user, h.action_type)]


def awaiting(user, request_filter: Optional[RequestFilter] = None):
    """Pending requests ``user`` can approve or reject right now, newest first.

    Who can decide depends on stages, slots, roles and delegations (``permissions.decision_slot``), which isn't
    expressible as one query. So the tenant's pending requests, excluding the user's own, are narrowed in SQL first
    and the exact rule is applied per request. Pending volumes are small; if they grow, cache or pre-compute per
    slot.
    """
    tenant = tenant_of(user)
    if tenant is None:
        return []
    candidates = (
        ApprovalRequest.objects.filter(tenant=tenant, status=RequestStatus.PENDING)
        .exclude(maker=user)
        .select_related("maker")
        .prefetch_related("stages__approvers")
    )
    rows = [r for r in candidates if decision_slot(user, r) is not None]
    return _filtered(rows, request_filter)


def made_by(user, *, open_only, request_filter: Optional[RequestFilter] = None):
    """The user's own requests: open ones (``open_only``) or resolved ones."""
    tenant = tenant_of(user)
    if tenant is None:
        return []
    statuses = RequestStatus.OPEN if open_only else RequestStatus.TERMINAL
    qs = ApprovalRequest.objects.filter(tenant=tenant, maker=user, status__in=statuses).select_related(
        "maker", "decided_by"
    )
    return _filtered(list(_ordered(qs, open_only)), request_filter)


def checker_history(user, request_filter: Optional[RequestFilter] = None):
    """Resolved requests a checker may review: ones they acted on, or of action types they approve."""
    tenant = tenant_of(user)
    if tenant is None:
        return []
    qs = ApprovalRequest.objects.filter(tenant=tenant, status__in=RequestStatus.TERMINAL)
    if not can_manage_policies(user):
        qs = qs.filter(
            Q(decisions__actor=user)
            | Q(decisions__on_behalf_of=user)
            | Q(action_type__in=approvable_action_types(user))
        ).distinct()
    return _filtered(list(_ordered(qs.select_related("maker", "decided_by"), False)), request_filter)


def makers(rows):
    """Distinct makers of ``rows``, for a maker filter."""
    adapter = get_adapter()
    return sorted({r.maker for r in rows}, key=lambda u: adapter.user_label(u).lower())


def open_requests_for(tenant, keys):
    """Open requests holding any of the item ``keys`` (``ActionHandler.lock_keys``), as ``{key: request}``.

    For marking items that are waiting for approval, e.g. a "Pending approval" badge on each affected row.
    """
    locks = ApprovalLock.objects.filter(tenant=tenant, key__in=list(keys)).select_related("request")
    return {lock.key: lock.request for lock in locks}


def open_count(user):
    """Number of the user's own open requests (pending or approved but not yet applied)."""
    return len(made_by(user, open_only=True))


def pending_count(user):
    """Awaiting-action count (sidebar badges), cached briefly and invalidated on every change."""
    tenant = tenant_of(user)
    if tenant is None:
        return 0
    key = f"approvals:pending:{tenant.pk}:{cache_version(tenant.pk)}:{user.pk}"
    cache = counts_cache()
    count = cache.get(key)
    if count is None:
        count = len(awaiting(user))
        cache.set(key, count, cache_timeout())
    return count


def _ordered(qs, open_only):
    # Open requests by submission; resolved ones by when they were decided, which is what a history reader scans by.
    return qs.order_by("-submitted_at") if open_only else qs.order_by("-decided_at", "-submitted_at")


def _filtered(rows, request_filter):
    return [r for r in rows if request_filter.matches(r)] if request_filter else rows


# --- one request --------------------------------------------------------------------------------


@dataclass(frozen=True)
class AllowedActions:
    """What a user may do on one request, for showing or hiding controls. The services re-check every action, so
    this is a display aid, not the enforcement."""

    decide: bool = False
    cancel: bool = False
    retry: bool = False
    resubmit: bool = False
    is_maker: bool = False


def allowed_actions(user, approval_request) -> AllowedActions:
    return AllowedActions(
        decide=can_decide(user, approval_request),
        cancel=can_cancel(user, approval_request),
        retry=can_retry(user, approval_request),
        resubmit=can_resubmit(user, approval_request),
        is_maker=user.pk == approval_request.maker_id,
    )


def _duration(seconds):
    # Compact countdown text: "2d 3h", "4h 05m" or "12m"; negative durations show as 0m.
    seconds = max(int(seconds), 0)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return _("%(days)sd %(hours)sh") % {"days": days, "hours": hours}
    if hours:
        return _("%(hours)sh %(minutes)sm") % {"hours": hours, "minutes": f"{minutes:02d}"}
    return _("%(minutes)sm") % {"minutes": minutes}


def _limit_text(minutes):
    # The SLA limit in the largest whole unit: "2 days", "6 hours" or "90 minutes".
    if minutes % 1440 == 0:
        days = minutes // 1440
        return ngettext("%(count)s day", "%(count)s days", days) % {"count": days}
    if minutes % 60 == 0:
        hours = minutes // 60
        return ngettext("%(count)s hour", "%(count)s hours", hours) % {"count": hours}
    return ngettext("%(count)s minute", "%(count)s minutes", minutes) % {"count": minutes}


@dataclass(frozen=True)
class SlaStatus:
    """SLA of a request's current stage, for display: ``state`` (SlaState), ``percent`` of the window used,
    ``remaining`` (or "overdue by …"), the ``limit``, and who it was ``escalated_to``."""

    state: str  # SlaState.*
    label: str
    percent: int = 0
    remaining: str = ""
    limit: str = ""
    due_at: Optional[object] = None
    started_at: Optional[object] = None
    escalated_to: str = ""


def sla_status(approval_request, now=None) -> SlaStatus:
    """Computed from the active stage's own window, so each step of an approval flow has a fresh SLA.

    A stage past its deadline shows as escalated (or overdue, before the SLA job has run). Otherwise it is at risk
    from 60% of the window and on track below that. Requests that aren't pending, or rules without an SLA, give NONE.
    """
    now = now or timezone.now()
    stage = approval_request.active_stage
    if stage is None or not stage.due_at or not stage.started_at:
        return SlaStatus(SlaState.NONE, SlaState.LABELS[SlaState.NONE])
    window = max((stage.due_at - stage.started_at).total_seconds(), 1)
    used = (now - stage.started_at).total_seconds() / window
    timing = {"limit": _limit_text(int(window // 60)), "due_at": stage.due_at, "started_at": stage.started_at}

    if stage.escalated_at or now >= stage.due_at:
        return SlaStatus(
            SlaState.ESCALATED,
            _("Escalated") if stage.escalated_at else _("Overdue"),
            percent=100,
            remaining=_("overdue by %(duration)s") % {"duration": _duration((now - stage.due_at).total_seconds())},
            escalated_to=_escalation_label(stage),
            **timing,
        )
    state = SlaState.AT_RISK if used >= SlaState.AT_RISK_RATIO else SlaState.ON_TRACK
    return SlaStatus(
        state,
        SlaState.LABELS[state],
        percent=min(int(used * 100), 100),
        remaining=_duration((stage.due_at - now).total_seconds()),
        **timing,
    )


def _escalation_label(stage):
    """Who an escalated stage went to, or "" when it hasn't escalated."""
    if not stage.escalated_at:
        return ""
    escalation = stage.approvers.filter(is_escalation=True).first()
    return escalation.approver_label if escalation else ""


@dataclass(frozen=True)
class StageProgress:
    """One stage for a progress strip: its approvers (and escalation approvers), approvals ``done`` of ``required``,
    and ``state``: ``done`` (approved), ``rejected`` (ended by a rejection), ``current`` (awaiting decisions) or
    ``waiting`` (not started)."""

    number: int
    approvers: list
    escalation: list
    required: int
    done: int
    require_all: bool
    state: str  # done | rejected | current | waiting


def stage_progress(approval_request):
    """All stages in order. ``required`` is how many regular approvers must approve (one, N of M, or all);
    escalation approvals count towards ``done``, capped at ``required``."""
    stages = approval_request.stages.prefetch_related("approvers__approver_user", "approvers__approver_role")
    rows = []
    for stage in stages:
        approvers = list(stage.approvers.all())
        regular = [a for a in approvers if not a.is_escalation]
        escalation = [a for a in approvers if a.is_escalation]
        required = stage.required_count(regular)
        done = min(sum(1 for a in approvers if a.satisfied_at), required)
        rows.append(
            StageProgress(
                number=stage.order + 1,
                approvers=regular,
                escalation=escalation,
                required=required,
                done=done,
                require_all=stage.require_all,
                state=_stage_state(approval_request, stage, done, required),
            )
        )
    return rows


def _stage_state(approval_request, stage, done, required):
    if stage.completed_at:
        # a rejection also completes the stage, without its approvals
        return "done" if done >= required else "rejected"
    if approval_request.status == RequestStatus.PENDING and stage.order == approval_request.current_stage:
        return "current"
    return "waiting"


def current_approvers_label(approval_request):
    """Who the request is waiting on: the unfilled slots of the active stage, e.g. "Reviewer, Chris Checker"."""
    stage = approval_request.active_stage
    if stage is None:
        return "—"
    return ", ".join(a.approver_label for a in stage.approvers.all() if not a.satisfied_at) or "—"


# --- policy history -----------------------------------------------------------------------------


def policy_history(tenant, action_type, scope_key=""):
    """Revisions of one rule, newest first: ``version``, ``change_type``, ``changes`` ([{field, before, after}]),
    ``snapshot``, ``reason``, ``actor`` and ``created``. Survives the rule being deleted."""
    return list(
        ApprovalPolicyRevision.objects.filter(tenant=tenant, action_type=action_type, scope_key=scope_key)
        .select_related("actor")
        .order_by("-version")
    )


@dataclass(frozen=True)
class PolicyVersionStatus:
    """The rule version a request was raised under and the rule's current version. ``raised_under`` is None for
    requests raised before rules were versioned."""

    raised_under: Optional[int]
    current: int

    @property
    def changed_since(self):
        return self.raised_under is not None and self.current > self.raised_under


def policy_version_status(approval_request) -> PolicyVersionStatus:
    """Which version of its rule a request was raised under, and whether the rule has changed since."""
    current = current_version(approval_request.tenant, approval_request.action_type, approval_request.scope_key)
    return PolicyVersionStatus(approval_request.policy_version, current)
