"""Who may decide on, cancel, view, or configure maker-checker requests.

Every decision check goes through ``decision_slot``/``can_decide`` so the host UI and the services module agree.
Segregation of duties is absolute here: the maker never decides on their own request and no one decides twice on
the same request, regardless of how the host grants permissions.
"""

from dataclasses import dataclass
from typing import Optional

from django.db.models import Q
from django.utils import timezone

from django_maker_checker.conf import get_adapter
from django_maker_checker.constants import RequestStatus
from django_maker_checker.models import ApprovalDelegation, same_db
from django_maker_checker.registry import get_handler

MANAGE_POLICIES = "manage_approval_policies"


def tenant_of(user):
    """The tenant ``user`` acts for, or None if they are not an active member."""
    if not user or not user.is_authenticated or not user.is_active:
        return None
    return get_adapter().tenant_of(user)


def is_member_of(user, tenant_id) -> bool:
    """Tenant isolation: every check below starts here, so users never act on another tenant's requests."""
    tenant = tenant_of(user)
    return tenant is not None and tenant.pk == tenant_id


def can_manage_policies(user) -> bool:
    """Administers rules: configure policies, cancel anyone's pending request, retry failed ones."""
    return tenant_of(user) is not None and get_adapter().has_permission(user, MANAGE_POLICIES)


def has_approve_permission(user, action_type) -> bool:
    return get_adapter().has_permission(user, get_handler(action_type).approve_permission)


def user_matches_slot(user, slot, role_ids=None) -> bool:
    """Whether ``user`` is the user, or holds the (active) role, named by an approver slot."""
    if slot.approver_user_id:
        return slot.approver_user_id == user.id
    if role_ids is None:
        role_ids = get_adapter().role_ids_of(user)
    return slot.approver_role_id in role_ids


def active_delegators(user, tenant_id, action_type, at=None):
    """Users who have currently delegated approvals for ``action_type`` to ``user``."""
    at = at or timezone.now()
    delegations = ApprovalDelegation.objects.filter(
        tenant_id=tenant_id, delegate=user, revoked_at__isnull=True, starts_at__lte=at, ends_at__gt=at
    ).select_related("delegator")
    return [d.delegator for d in delegations if d.covers(action_type, at)]


@dataclass
class Slot:
    """The stage slot a decision would fill; ``on_behalf_of`` is the delegator when acting by delegation."""

    approver: object  # ApprovalStageApprover
    on_behalf_of: Optional[object] = None


def has_decided(user, approval_request) -> bool:
    """Whether ``user`` ever decided on this request (in any round), directly or through a delegate."""
    decisions = same_db(approval_request, approval_request.decisions)
    return decisions.filter(Q(actor=user) | Q(on_behalf_of=user)).exists()


def decided_this_round(user, approval_request) -> bool:
    """Whether ``user`` already decided in the request's current round. One person may act once per round, so the
    same person can't satisfy two steps of an approval flow; after a resubmission everyone may decide again."""
    decisions = same_db(approval_request, approval_request.decisions).filter(submission=approval_request.submission)
    return decisions.filter(Q(actor=user) | Q(on_behalf_of=user)).exists()


def decision_slot(user, approval_request) -> Optional[Slot]:
    """The approver slot ``user`` would fill on the current stage, or None if they can't decide.

    The user's own authority is tried first: they must be able to take part (see ``_may_take_part``), hold the
    action's approve permission, and be named on an unfilled slot, directly or through a role. Failing that, each
    current delegator is tried under exactly the same rules, and a match is recorded as acting on their behalf.
    """
    if approval_request.status != RequestStatus.PENDING or not _may_take_part(user, approval_request):
        return None
    stage = approval_request.active_stage
    if stage is None:
        return None
    open_slots = [slot for slot in same_db(stage, stage.approvers).all() if not slot.satisfied_at]

    own_slot = _open_slot_for(user, approval_request, open_slots)
    if own_slot is not None:
        return Slot(own_slot)

    for delegator in active_delegators(user, approval_request.tenant_id, approval_request.action_type):
        if not _may_take_part(delegator, approval_request):
            continue
        delegated_slot = _open_slot_for(delegator, approval_request, open_slots)
        if delegated_slot is not None:
            return Slot(delegated_slot, on_behalf_of=delegator)
    return None


def _may_take_part(person, approval_request) -> bool:
    """Segregation of duties: a member of the request's tenant who is not its maker (no exception for superusers or
    staff) and has not already acted on it in this round."""
    return (
        is_member_of(person, approval_request.tenant_id)
        and person.id != approval_request.maker_id
        and not decided_this_round(person, approval_request)
    )


def _open_slot_for(person, approval_request, open_slots):
    """The first unfilled slot ``person`` may fill with their own authority, or None."""
    if not has_approve_permission(person, approval_request.action_type):
        return None
    role_ids = get_adapter().role_ids_of(person)
    for slot in open_slots:
        if user_matches_slot(person, slot, role_ids):
            return slot
    return None


def can_decide(user, approval_request) -> bool:
    return decision_slot(user, approval_request) is not None


def can_cancel(user, approval_request) -> bool:
    """The maker may withdraw their own pending request (or one sent back for changes); policy administrators may
    cancel any."""
    if approval_request.status not in RequestStatus.CANCELLABLE or not is_member_of(user, approval_request.tenant_id):
        return False
    return user.id == approval_request.maker_id or can_manage_policies(user)


def can_resubmit(user, approval_request) -> bool:
    """Only the maker resubmits a request that was sent back for changes."""
    return (
        approval_request.status == RequestStatus.CHANGES_REQUESTED
        and user.id == approval_request.maker_id
        and is_member_of(user, approval_request.tenant_id)
    )


def can_retry(user, approval_request) -> bool:
    """Re-applying an approved change is an administrator's call, after fixing whatever made it fail."""
    if approval_request.status != RequestStatus.FAILED:
        return False
    return is_member_of(user, approval_request.tenant_id) and can_manage_policies(user)


def can_view(user, approval_request) -> bool:
    """Who may open a request: its maker, policy administrators, anyone who acted on it, anyone who approves this
    kind of action, and current delegates of such approvers."""
    if not is_member_of(user, approval_request.tenant_id):
        return False
    if user.id == approval_request.maker_id or can_manage_policies(user):
        return True
    if has_decided(user, approval_request) or has_approve_permission(user, approval_request.action_type):
        return True
    return bool(active_delegators(user, approval_request.tenant_id, approval_request.action_type))


def eligible_checkers(tenant, action_type, approver_slots, exclude_user_ids=()):
    """Active members of ``tenant`` who could fill any of ``approver_slots`` (ignores delegation).

    Used to refuse a submission nobody but the maker could approve, and to choose who to notify.
    """
    adapter = get_adapter()
    perm = get_handler(action_type).approve_permission
    user_ids = {s.approver_user_id for s in approver_slots if s.approver_user_id}
    role_ids = {s.approver_role_id for s in approver_slots if s.approver_role_id}
    if not user_ids and not role_ids:
        return []
    users = adapter.members(tenant, user_ids=user_ids, role_ids=role_ids).exclude(pk__in=list(exclude_user_ids))
    return [u for u in users if adapter.has_permission(u, perm)]


def approver_choices(tenant, action_type, query=""):
    """Users and roles that can be named as approvers for ``action_type`` (they hold its permission), optionally
    narrowed by a case-insensitive ``query`` on their names."""
    adapter = get_adapter()
    perm = get_handler(action_type).approve_permission

    users = [
        user
        for user in adapter.members(tenant)
        if adapter.has_permission(user, perm)
        and _matches_query(query, adapter.user_label(user), user.get_username(), getattr(user, "email", ""))
    ]
    roles = [
        role
        for role in adapter.roles(tenant)
        if adapter.role_has_permission(role, perm) and _matches_query(query, adapter.role_label(role))
    ]
    users.sort(key=lambda user: adapter.user_label(user).lower())
    roles.sort(key=lambda role: adapter.role_label(role).lower())
    return users, roles


def _matches_query(query, *texts):
    query = (query or "").strip().lower()
    return not query or any(query in (text or "").lower() for text in texts)
