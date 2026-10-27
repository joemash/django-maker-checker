"""Fixed vocabularies of the engine: request statuses, workflows, SLA units and states, risk levels, decisions,
timeline event types and policy change types. Values are stored in the database, so they must not be renamed; labels
are translatable."""

from django.utils.translation import gettext_lazy as _


class RequestStatus:
    PENDING = "pending"
    CHANGES_REQUESTED = "changes_requested"
    APPROVED = "approved"
    EXECUTED = "executed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    FAILED = "failed"

    CHOICES = [
        (PENDING, _("Pending")),
        (CHANGES_REQUESTED, _("Changes Requested")),
        (APPROVED, _("Approved")),
        (EXECUTED, _("Approved & Executed")),
        (REJECTED, _("Rejected")),
        (CANCELLED, _("Cancelled")),
        (FAILED, _("Execution Failed")),
    ]
    # Statuses that hold the resource lock: a second request for the same target is refused.
    OPEN = (PENDING, CHANGES_REQUESTED, APPROVED)
    # Statuses the maker can still withdraw.
    CANCELLABLE = (PENDING, CHANGES_REQUESTED)
    TERMINAL = (EXECUTED, REJECTED, CANCELLED, FAILED)


class WorkflowType:
    """How a policy's approver slots become stages: SINGLE and MULTI are one stage (MULTI with any-one or all
    approvers, per ``require_all``); SEQUENTIAL is one stage per slot, in order."""

    SINGLE = "single"
    MULTI = "multi"
    SEQUENTIAL = "sequential"

    CHOICES = [
        (SINGLE, _("Single Approver")),
        (MULTI, _("Multiple Approvers")),
        (SEQUENTIAL, _("Approval Flow")),
    ]


class SlaUnit:
    MINUTES = "minutes"
    HOURS = "hours"
    DAYS = "days"

    CHOICES = [(MINUTES, _("Minutes")), (HOURS, _("Hours")), (DAYS, _("Days"))]
    TO_MINUTES = {MINUTES: 1, HOURS: 60, DAYS: 60 * 24}


class RiskLevel:
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    CHOICES = [(LOW, _("Low")), (MEDIUM, _("Medium")), (HIGH, _("High"))]


class Decision:
    APPROVE = "approve"
    REJECT = "reject"
    REQUEST_CHANGES = "changes"  # send back to the maker to amend and resubmit

    CHOICES = [(APPROVE, _("Approve")), (REJECT, _("Reject")), (REQUEST_CHANGES, _("Request changes"))]


class EventType:
    CREATED = "created"
    NOTIFIED = "notified"
    VIEWED = "viewed"
    SLA_WARNING = "sla_warning"
    ESCALATED = "escalated"
    STAGE_APPROVED = "stage_approved"
    APPROVED = "approved"
    REJECTED = "rejected"
    CHANGES_REQUESTED = "changes_requested"
    RESUBMITTED = "resubmitted"
    CANCELLED = "cancelled"
    COMMENT = "comment"
    DELEGATED = "delegated"
    EXECUTED = "executed"
    FAILED = "failed"
    RETRIED = "retried"
    REASSIGNED = "reassigned"

    CHOICES = [
        (CREATED, _("Submitted")),
        (NOTIFIED, _("Notified")),
        (VIEWED, _("Viewed")),
        (SLA_WARNING, _("SLA Warning")),
        (ESCALATED, _("Escalated")),
        (STAGE_APPROVED, _("Stage Approved")),
        (APPROVED, _("Approved")),
        (REJECTED, _("Rejected")),
        (CHANGES_REQUESTED, _("Changes Requested")),
        (RESUBMITTED, _("Resubmitted")),
        (CANCELLED, _("Cancelled")),
        (COMMENT, _("Comment")),
        (DELEGATED, _("Delegated")),
        (EXECUTED, _("Executed")),
        (FAILED, _("Execution Failed")),
        (RETRIED, _("Retried")),
        (REASSIGNED, _("Reassigned")),
    ]


class SlaState:
    """Display state of a stage's SLA, derived from elapsed time; it is never stored."""

    ON_TRACK = "on_track"
    AT_RISK = "at_risk"
    ESCALATED = "escalated"
    NONE = "none"

    LABELS = {ON_TRACK: _("On Track"), AT_RISK: _("At Risk"), ESCALATED: _("Escalated"), NONE: _("No SLA")}
    # Share of the SLA window after which a request is shown as at risk / gets a reminder.
    AT_RISK_RATIO = 0.6
    WARNING_RATIO = 0.75


class PolicyChange:
    CREATED = "created"
    UPDATED = "updated"
    ENABLED = "enabled"
    DISABLED = "disabled"
    DELETED = "deleted"

    CHOICES = [
        (CREATED, _("Created")),
        (UPDATED, _("Updated")),
        (ENABLED, _("Enabled")),
        (DISABLED, _("Disabled")),
        (DELETED, _("Deleted")),
    ]
