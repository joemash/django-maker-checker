"""Fixed vocabularies of the engine: request statuses, workflows, SLA units and states, risk levels, decisions,
timeline event types and policy change types. Values are stored in the database, so they must not be renamed."""


class RequestStatus:
    PENDING = "pending"
    APPROVED = "approved"
    EXECUTED = "executed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    FAILED = "failed"

    CHOICES = [
        (PENDING, "Pending"),
        (APPROVED, "Approved"),
        (EXECUTED, "Approved & Executed"),
        (REJECTED, "Rejected"),
        (CANCELLED, "Cancelled"),
        (FAILED, "Execution Failed"),
    ]
    # Statuses that hold the resource lock: a second request for the same target is refused.
    OPEN = (PENDING, APPROVED)
    TERMINAL = (EXECUTED, REJECTED, CANCELLED, FAILED)


class WorkflowType:
    """How a policy's approver slots become stages: SINGLE and MULTI are one stage (MULTI with any-one or all
    approvers, per ``require_all``); SEQUENTIAL is one stage per slot, in order."""

    SINGLE = "single"
    MULTI = "multi"
    SEQUENTIAL = "sequential"

    CHOICES = [
        (SINGLE, "Single Approver"),
        (MULTI, "Multiple Approvers"),
        (SEQUENTIAL, "Approval Flow"),
    ]


class SlaUnit:
    MINUTES = "minutes"
    HOURS = "hours"
    DAYS = "days"

    CHOICES = [(MINUTES, "Minutes"), (HOURS, "Hours"), (DAYS, "Days")]
    TO_MINUTES = {MINUTES: 1, HOURS: 60, DAYS: 60 * 24}


class RiskLevel:
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    CHOICES = [(LOW, "Low"), (MEDIUM, "Medium"), (HIGH, "High")]


class Decision:
    APPROVE = "approve"
    REJECT = "reject"

    CHOICES = [(APPROVE, "Approve"), (REJECT, "Reject")]


class EventType:
    CREATED = "created"
    NOTIFIED = "notified"
    VIEWED = "viewed"
    SLA_WARNING = "sla_warning"
    ESCALATED = "escalated"
    STAGE_APPROVED = "stage_approved"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    COMMENT = "comment"
    DELEGATED = "delegated"
    EXECUTED = "executed"
    FAILED = "failed"
    RETRIED = "retried"

    CHOICES = [
        (CREATED, "Submitted"),
        (NOTIFIED, "Notified"),
        (VIEWED, "Viewed"),
        (SLA_WARNING, "SLA Warning"),
        (ESCALATED, "Escalated"),
        (STAGE_APPROVED, "Stage Approved"),
        (APPROVED, "Approved"),
        (REJECTED, "Rejected"),
        (CANCELLED, "Cancelled"),
        (COMMENT, "Comment"),
        (DELEGATED, "Delegated"),
        (EXECUTED, "Executed"),
        (FAILED, "Execution Failed"),
        (RETRIED, "Retried"),
    ]


class SlaState:
    """Display state of a stage's SLA, derived from elapsed time; it is never stored."""

    ON_TRACK = "on_track"
    AT_RISK = "at_risk"
    ESCALATED = "escalated"
    NONE = "none"

    LABELS = {ON_TRACK: "On Track", AT_RISK: "At Risk", ESCALATED: "Escalated", NONE: "No SLA"}
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
        (CREATED, "Created"),
        (UPDATED, "Updated"),
        (ENABLED, "Enabled"),
        (DISABLED, "Disabled"),
        (DELETED, "Deleted"),
    ]
