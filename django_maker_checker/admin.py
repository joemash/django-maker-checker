"""Django admin views of maker-checker data, for inspection by staff.

Every page here is read-only. Changes must go through ``services`` and ``policies``, which enforce the rules
(segregation of duties, approver eligibility) and record the audit trail (request events, policy revisions, host
audit records). An editable admin would let staff bypass both.
"""

from django.contrib import admin

from django_maker_checker import models


class ReadOnlyAdmin(admin.ModelAdmin):
    """Shows every field and allows no add, change or delete."""

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class ReadOnlyInline(admin.TabularInline):
    extra = 0
    can_delete = False

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request, obj=None):
        return False


class PolicyApproverInline(ReadOnlyInline):
    model = models.ApprovalPolicyApprover


class RevisionInline(ReadOnlyInline):
    model = models.ApprovalPolicyRevision
    fields = ("version", "change_type", "changes", "reason", "actor", "created")


@admin.register(models.ApprovalPolicy)
class ApprovalPolicyAdmin(ReadOnlyAdmin):
    list_display = ("tenant", "action_type", "scope_key", "workflow_type", "is_enabled", "sla_enabled", "updated")
    list_filter = ("action_type", "workflow_type", "is_enabled")
    inlines = [PolicyApproverInline, RevisionInline]


@admin.register(models.ApprovalPolicyRevision)
class ApprovalPolicyRevisionAdmin(ReadOnlyAdmin):
    """All revisions, including those of deleted rules (which no longer appear under a policy)."""

    list_display = ("tenant", "action_type", "scope_key", "version", "change_type", "actor", "created")
    list_filter = ("action_type", "change_type")


class StageInline(ReadOnlyInline):
    model = models.ApprovalStage


class DecisionInline(ReadOnlyInline):
    model = models.ApprovalDecision


class EventInline(ReadOnlyInline):
    model = models.ApprovalEvent


@admin.register(models.ApprovalRequest)
class ApprovalRequestAdmin(ReadOnlyAdmin):
    list_display = ("reference", "tenant", "action_type", "target_label", "maker", "status", "submitted_at")
    list_filter = ("status", "action_type")
    search_fields = ("reference", "target_label", "maker__email", "maker__username")
    inlines = [StageInline, DecisionInline, EventInline]


@admin.register(models.ApprovalDelegation)
class ApprovalDelegationAdmin(ReadOnlyAdmin):
    list_display = ("tenant", "delegator", "delegate", "starts_at", "ends_at", "revoked_at")
