"""Maker-checker domain model.

An ``ApprovalPolicy`` says which approvers must sign off on an action type for a tenant, either for the whole
tenant (``scope_key == ""``) or for one host-defined scope. When a maker performs a covered action an
``ApprovalRequest`` is created instead of applying the change; the proposed change lives in ``payload`` and is
applied by the action's handler only after the final approval.

Request status is a small explicit state machine (django-fsm). Stage progression for multi-stage and
multi-approver workflows is data (``ApprovalStage`` / ``ApprovalStageApprover``), so the state machine stays the
same size whatever the policy shape.

The tenant and role models are configurable (``APPROVALS_TENANT_MODEL`` / ``APPROVALS_ROLE_MODEL``), the same way
``AUTH_USER_MODEL`` is. Without them, the built-in ``Tenant`` (one row, for single-tenant projects) and Django's
``auth.Group`` are used.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_fsm import ConcurrentTransitionMixin, FSMField, transition

from django_maker_checker.conf import get_adapter, reference_prefix
from django_maker_checker.constants import (
    Decision,
    EventType,
    PolicyChange,
    RequestStatus,
    RiskLevel,
    SlaUnit,
    WorkflowType,
)

User = settings.AUTH_USER_MODEL
TENANT_MODEL = settings.APPROVALS_TENANT_MODEL
ROLE_MODEL = settings.APPROVALS_ROLE_MODEL


class Tenant(models.Model):
    """The built-in tenant, for projects that don't have tenants of their own.

    A single-tenant project uses one row, ``Tenant.default()``, which owns every rule and request. It is swappable
    like ``AUTH_USER_MODEL``: when ``APPROVALS_TENANT_MODEL`` names your own model, this one has no table.
    """

    name = models.CharField(max_length=100, unique=True)

    class Meta:
        swappable = "APPROVALS_TENANT_MODEL"

    def __str__(self):
        return self.name

    @classmethod
    def default(cls):
        tenant, _created = cls.objects.get_or_create(name="default")
        return tenant


class ApproverTargetMixin(models.Model):
    """An approver slot is either one user or every member of a tenant role."""

    approver_user = models.ForeignKey(User, null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    approver_role = models.ForeignKey(ROLE_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name="+")

    class Meta:
        abstract = True

    def clean(self):
        # A slot naming both (or neither) would make "who may fill it" ambiguous in decision checks.
        if bool(self.approver_user_id) == bool(self.approver_role_id):
            raise ValidationError("Exactly one of approver_user or approver_role must be set.")

    @property
    def approver_token(self):
        """Serialised form used by approver pickers and ``PolicySpec``: ``u:<id>`` or ``r:<id>``."""
        if self.approver_user_id:
            return f"u:{self.approver_user_id}"
        return f"r:{self.approver_role_id}" if self.approver_role_id else ""

    @property
    def approver_label(self):
        """Display name, resolved through the host adapter so the host controls how people and roles are named."""
        adapter = get_adapter()
        if self.approver_user_id:
            return adapter.user_label(self.approver_user)
        return adapter.role_label(self.approver_role) if self.approver_role_id else ""


class ApprovalPolicy(models.Model):
    """The approval rule for one action type in one tenant, tenant-wide or for one host-defined scope.

    A policy only gates an action when it is enabled *and* has at least one approver (see
    ``services.find_policy``); an unconfigured policy, e.g. a trigger just added from a trigger bank, has no effect.
    Change policies through ``django_maker_checker.policies`` so every change is validated and recorded as a revision.
    """

    tenant = models.ForeignKey(TENANT_MODEL, on_delete=models.CASCADE, related_name="approval_policies")
    action_type = models.CharField(max_length=64, help_text=_("Registry key of the action handler"))
    scope_key = models.CharField(max_length=128, blank=True, default="", help_text=_("Empty for tenant-wide rules"))
    is_enabled = models.BooleanField(default=True)
    workflow_type = models.CharField(max_length=16, choices=WorkflowType.CHOICES, default=WorkflowType.SINGLE)
    require_all = models.BooleanField(
        default=False, help_text=_("Multiple approvers only: every approver must approve (otherwise any one)")
    )
    required_approvals = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text=_("Multiple approvers only: how many of the approvers must approve (N of M)")
    )
    risk_level = models.CharField(max_length=10, choices=RiskLevel.CHOICES, default=RiskLevel.MEDIUM)
    conditions = models.JSONField(default=dict, blank=True, help_text=_("Handler-specific conditions"))

    sla_enabled = models.BooleanField(default=False)
    sla_value = models.PositiveIntegerField(default=6)
    sla_unit = models.CharField(max_length=10, choices=SlaUnit.CHOICES, default=SlaUnit.HOURS)
    escalation_user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    escalation_role = models.ForeignKey(ROLE_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    updated_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Approval Policy")
        verbose_name_plural = _("Approval Policies")
        # One rule per action and scope. ``scope_key`` is "" (not NULL) for tenant-wide rules so this constraint
        # also covers them; NULLs would be treated as distinct and allow duplicates.
        constraints = [
            models.UniqueConstraint(fields=["tenant", "action_type", "scope_key"], name="approvals_policy_unique"),
        ]

    def __str__(self):
        return f"{self.action_type} ({self.scope_key or 'global'}) – {self.tenant}"

    @property
    def is_global(self):
        return not self.scope_key

    @property
    def sla_minutes(self):
        """The SLA window per stage in minutes, or None when the rule has no SLA."""
        if not self.sla_enabled:
            return None
        return self.sla_value * SlaUnit.TO_MINUTES[self.sla_unit]

    @property
    def escalation_token(self):
        """The escalation backup as a ``u:<id>`` / ``r:<id>`` token ("" if none), the form stored in snapshots."""
        if self.escalation_user_id:
            return f"u:{self.escalation_user_id}"
        return f"r:{self.escalation_role_id}" if self.escalation_role_id else ""

    def ordered_approvers(self):
        """Approver slots in step order; for approval flows the position is the step number."""
        return list(self.approvers.select_related("approver_user", "approver_role").order_by("order", "id"))

    def is_configured(self):
        return self.approvers.exists()

    def snapshot(self):
        """Frozen copy stored on each request so later policy edits don't change in-flight requests."""
        return {
            "id": self.id,
            "workflow_type": self.workflow_type,
            "require_all": self.require_all,
            "required_approvals": self.required_approvals,
            "risk_level": self.risk_level,
            "sla_minutes": self.sla_minutes,
            "escalation": self.escalation_token,
            "approvers": [a.approver_token for a in self.ordered_approvers()],
        }


class ApprovalPolicyApprover(ApproverTargetMixin):
    """One approver slot on a policy. How slots become stages depends on the workflow (``services._stage_layout``)."""

    policy = models.ForeignKey(ApprovalPolicy, on_delete=models.CASCADE, related_name="approvers")
    order = models.PositiveIntegerField(default=0, help_text=_("Step number for approval flows"))

    class Meta:
        ordering = ["order", "id"]


class ApprovalPolicyRevision(models.Model):
    """Append-only history of an approval rule: what it was after each change, who changed it, when and why.

    The rule is identified by tenant, action type and scope, so its history (and version numbering) continues across a
    policy being deleted and created again. ``changes`` holds human-readable before/after values, so the history stays
    meaningful after approvers, roles or the policy itself are gone.
    """

    policy = models.ForeignKey(
        ApprovalPolicy, null=True, blank=True, on_delete=models.SET_NULL, related_name="revisions"
    )
    tenant = models.ForeignKey(TENANT_MODEL, on_delete=models.CASCADE, related_name="approval_policy_revisions")
    action_type = models.CharField(max_length=64)
    scope_key = models.CharField(max_length=128, blank=True, default="")
    version = models.PositiveIntegerField()
    change_type = models.CharField(max_length=10, choices=PolicyChange.CHOICES)
    snapshot = models.JSONField(default=dict, help_text=_("The rule after the change (before it, for deletions)"))
    changes = models.JSONField(default=list, help_text=_("[{field, before, after}] in display form"))
    reason = models.TextField(blank=True)
    actor = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "action_type", "scope_key", "version"], name="approvals_policy_revision_version"
            )
        ]

    def __str__(self):
        return f"{self.action_type} ({self.scope_key or 'global'}) v{self.version} {self.change_type}"


class ApprovalRequest(ConcurrentTransitionMixin, models.Model):
    """One maker's proposed change, waiting for (or resolved by) checkers.

    Everything needed to decide and apply the change is frozen on the request when it is submitted: the payload,
    the target's ``before_snapshot``, the policy's ``policy_snapshot`` and its stages. Later edits to the policy or
    the target therefore never change what checkers are approving. ``ConcurrentTransitionMixin`` makes a save fail
    if another process changed the status in the meantime, on top of the row locks taken by ``services``.
    """

    tenant = models.ForeignKey(TENANT_MODEL, on_delete=models.CASCADE, related_name="approval_requests")
    reference = models.CharField(max_length=32, unique=True, null=True, blank=True)
    action_type = models.CharField(max_length=64)
    policy = models.ForeignKey(ApprovalPolicy, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    policy_snapshot = models.JSONField(default=dict)
    policy_version = models.PositiveIntegerField(
        null=True, blank=True, help_text=_("Rule version the request was raised under")
    )
    scope_key = models.CharField(max_length=128, blank=True, default="")

    target_label = models.CharField(max_length=255, blank=True, help_text=_("Human readable subject of the change"))
    lock_key = models.CharField(max_length=255, help_text=_("Identifies the locked resource while open"))
    payload = models.JSONField(default=dict, help_text=_("Proposed change, applied on approval"))
    before_snapshot = models.JSONField(default=dict, help_text=_("State of the target when the request was raised"))
    result = models.JSONField(default=dict, blank=True, help_text=_("Outcome reported by the handler on execution"))
    justification = models.TextField(blank=True)
    risk_level = models.CharField(max_length=10, choices=RiskLevel.CHOICES, default=RiskLevel.MEDIUM)

    maker = models.ForeignKey(User, on_delete=models.PROTECT, related_name="approval_requests_made")
    status = FSMField(default=RequestStatus.PENDING, choices=RequestStatus.CHOICES, protected=True, db_index=True)
    current_stage = models.PositiveIntegerField(default=0)
    submission = models.PositiveIntegerField(default=1, help_text=_("Round of approval; goes up on each resubmission"))

    submitted_at = models.DateTimeField(default=timezone.now)
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    executed_at = models.DateTimeField(null=True, blank=True)
    execution_error = models.TextField(blank=True)
    rejection_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-submitted_at"]
        # At most one open request per item (e.g. one pending "delete user X"), enforced by the database so two
        # makers submitting at the same moment can't both succeed. Closed requests don't count, which releases the
        # lock as soon as a request is executed, rejected, cancelled or fails.
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "action_type", "lock_key"],
                condition=Q(status__in=RequestStatus.OPEN),
                name="approvals_request_one_open_per_resource",
            )
        ]
        indexes = [models.Index(fields=["tenant", "status"], name="approvals_tenant_status_idx")]

    def __str__(self):
        return self.reference or f"request #{self.pk}"

    def save(self, *args, **kwargs):
        """Assign the human reference (``REQ-YYMM-00042``, prefix from ``APPROVALS_REFERENCE_PREFIX``) on first save.

        The reference embeds the primary key, which only exists after the insert, so it is written with a second,
        targeted UPDATE rather than a full save (which would re-run the concurrent-transition check).
        """
        creating = self.pk is None
        super().save(*args, **kwargs)
        if creating and not self.reference:
            self.reference = f"{reference_prefix()}-{self.submitted_at:%y%m}-{self.pk:05d}"
            type(self).objects.filter(pk=self.pk).update(reference=self.reference)

    # --- state machine -------------------------------------------------------------------------
    # Only the services module calls these; nothing else assigns ``status`` (the field is protected).
    #
    #   PENDING ─approve→ APPROVED ─mark_executed→ EXECUTED
    #      │                 └──────mark_failed→ FAILED ─retry→ APPROVED
    #      ├─request_changes→ CHANGES_REQUESTED ─resubmit→ PENDING (a new round)
    #      ├─reject→ REJECTED
    #      └─cancel→ CANCELLED   (also from CHANGES_REQUESTED)
    #
    # APPROVED is short-lived: the decision is final, and applying the change is a separate step so a failure to
    # apply never loses the approval.

    @transition(field=status, source=RequestStatus.PENDING, target=RequestStatus.APPROVED)
    def approve(self, by):
        self.decided_at = timezone.now()
        self.decided_by = by

    @transition(field=status, source=RequestStatus.PENDING, target=RequestStatus.REJECTED)
    def reject(self, by, reason):
        """Reject with a mandatory reason; the maker sees it, and it stays on the audit trail."""
        if not (reason or "").strip():
            raise ValidationError(_("A reason is required to reject a request."))
        self.decided_at = timezone.now()
        self.decided_by = by
        self.rejection_reason = reason.strip()

    @transition(field=status, source=RequestStatus.PENDING, target=RequestStatus.CHANGES_REQUESTED)
    def request_changes(self, note):
        """Send the request back to its maker to amend and resubmit; a note saying what to change is required."""
        if not (note or "").strip():
            raise ValidationError(_("Say what needs to change."))

    @transition(field=status, source=RequestStatus.CHANGES_REQUESTED, target=RequestStatus.PENDING)
    def resubmit(self):
        """Start a new round of approval; decisions from earlier rounds no longer count."""
        self.submission += 1

    @transition(field=status, source=list(RequestStatus.CANCELLABLE), target=RequestStatus.CANCELLED)
    def cancel(self, by):
        self.decided_at = timezone.now()
        self.decided_by = by

    @transition(field=status, source=RequestStatus.APPROVED, target=RequestStatus.EXECUTED)
    def mark_executed(self, result=None):
        self.executed_at = timezone.now()
        self.execution_error = ""
        self.result = result or {}

    @transition(field=status, source=RequestStatus.APPROVED, target=RequestStatus.FAILED)
    def mark_failed(self, error):
        """Applying the change raised; the approval stands and the request can be retried."""
        self.execution_error = str(error)[:4000]

    @transition(field=status, source=RequestStatus.FAILED, target=RequestStatus.APPROVED)
    def retry(self):
        self.execution_error = ""

    # --- helpers -------------------------------------------------------------------------------

    @property
    def is_open(self):
        return self.status in RequestStatus.OPEN

    @property
    def active_stage(self):
        """The stage checkers act on now, or None once the request is no longer pending."""
        if self.status != RequestStatus.PENDING:
            return None
        return self.stages.filter(order=self.current_stage).first()


class ApprovalLock(models.Model):
    """One item an open request is about to change, so no other open request can claim it.

    A request holds one lock per key its handler returns from ``lock_keys`` (one per affected item, e.g. every
    transaction in a manual match). Locks are taken in the same transaction as the request and released when it
    closes; the unique constraint turns a second claim on the same item into a readable error.
    """

    tenant = models.ForeignKey(TENANT_MODEL, on_delete=models.CASCADE, related_name="+")
    key = models.CharField(max_length=255)
    request = models.ForeignKey("ApprovalRequest", on_delete=models.CASCADE, related_name="locks")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "key"], name="approvals_lock_unique_key")]

    def __str__(self):
        return self.key


def required_approvals_count(slot_count, require_all, required_approvals):
    """Approvals a stage needs from ``slot_count`` approver slots: N when the rule says N of M, every slot when it
    requires all, otherwise one."""
    if required_approvals:
        return min(required_approvals, slot_count)
    return slot_count if require_all else 1


class ApprovalStage(models.Model):
    """One step of a request's approval, materialised from the policy at submission.

    Stages run in ``order``; only the stage at ``request.current_stage`` is active. ``started_at`` and ``due_at`` are
    set when a stage becomes active, so each step of an approval flow gets its own SLA window. ``sla_warned_at`` and
    ``escalated_at`` make the SLA reminder and escalation happen once per stage.
    """

    request = models.ForeignKey(ApprovalRequest, on_delete=models.CASCADE, related_name="stages")
    order = models.PositiveIntegerField()
    require_all = models.BooleanField(default=False)
    required_approvals = models.PositiveSmallIntegerField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    due_at = models.DateTimeField(null=True, blank=True)
    sla_warned_at = models.DateTimeField(null=True, blank=True)
    escalated_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["order"]
        constraints = [models.UniqueConstraint(fields=["request", "order"], name="approvals_stage_unique_order")]

    def __str__(self):
        return f"{self.request} stage {self.order + 1}"

    def required_count(self, regular_slots):
        """How many of the stage's regular (non-escalation) slots must approve."""
        return required_approvals_count(len(regular_slots), self.require_all, self.required_approvals)


class ApprovalStageApprover(ApproverTargetMixin):
    """An approver slot on a stage, copied from the policy (or added by SLA escalation).

    ``satisfied_at`` marks the slot as filled. An escalation slot completes its stage on its own, even when the stage
    requires all regular approvers, because escalation exists to unblock a stalled step.
    """

    stage = models.ForeignKey(ApprovalStage, on_delete=models.CASCADE, related_name="approvers")
    is_escalation = models.BooleanField(default=False, help_text=_("Added when the stage SLA was breached"))
    satisfied_at = models.DateTimeField(null=True, blank=True)
    satisfied_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["is_escalation", "id"]


class ApprovalDecision(models.Model):
    """One approve/reject by one person. With delegation, ``actor`` acted and ``on_behalf_of`` held the authority."""

    request = models.ForeignKey(ApprovalRequest, on_delete=models.CASCADE, related_name="decisions")
    stage = models.ForeignKey(ApprovalStage, on_delete=models.CASCADE, related_name="decisions")
    actor = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    on_behalf_of = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", help_text=_("Set when delegated")
    )
    decision = models.CharField(max_length=10, choices=Decision.CHOICES)
    submission = models.PositiveIntegerField(default=1, help_text=_("The request's round of approval it belongs to"))
    comment = models.TextField(blank=True)
    created = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["created"]


class ApprovalEvent(models.Model):
    """Activity timeline of a request; also mirrored to the host's audit log."""

    request = models.ForeignKey(ApprovalRequest, on_delete=models.CASCADE, related_name="events")
    type = models.CharField(max_length=20, choices=EventType.CHOICES)
    actor = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    message = models.TextField()
    data = models.JSONField(default=dict, blank=True)
    created = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["created", "id"]


class ApprovalDelegation(models.Model):
    """Lets ``delegate`` act on approvals assigned to ``delegator`` during a time window."""

    tenant = models.ForeignKey(TENANT_MODEL, on_delete=models.CASCADE, related_name="approval_delegations")
    delegator = models.ForeignKey(User, on_delete=models.CASCADE, related_name="approval_delegations_given")
    delegate = models.ForeignKey(User, on_delete=models.CASCADE, related_name="approval_delegations_received")
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    action_types = models.JSONField(default=list, blank=True, help_text=_("Empty means all action types"))
    reason = models.TextField(blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="+")
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-starts_at"]

    def clean(self):
        if self.delegator_id and self.delegator_id == self.delegate_id:
            raise ValidationError(_("You cannot delegate to yourself."))
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValidationError(_("The end of the delegation must be after its start."))

    def covers(self, action_type, at=None):
        """Whether the delegation applies to ``action_type`` at ``at`` (now by default): not revoked, inside the
        window (end exclusive), and either unrestricted or listing the action."""
        at = at or timezone.now()
        if self.revoked_at or not (self.starts_at <= at < self.ends_at):
            return False
        return not self.action_types or action_type in self.action_types

    @property
    def is_current(self):
        return not self.revoked_at and self.starts_at <= timezone.now() < self.ends_at
