from django.contrib.auth import get_user_model
from django.db.models import Q

from django_maker_checker.adapter import ApprovalsAdapter
from tests.testapp.models import Member, Role


class TestAdapter(ApprovalsAdapter):
    """In-memory sinks so tests can assert on notifications and audit records."""

    outbox: list = []
    audit_log: list = []
    disabled_tenant_ids: set = set()  # tenants with maker-checker switched off

    def _member(self, user):
        return Member.objects.filter(user=user, removed=False).select_related("tenant", "role").first()

    def tenant_of(self, user):
        member = self._member(user)
        return member.tenant if member and user.is_active else None

    def members(self, tenant, user_ids=None, role_ids=None):
        qs = get_user_model().objects.filter(member__tenant=tenant, member__removed=False, is_active=True)
        if user_ids is not None or role_ids is not None:
            by_role = Q(member__role_id__in=list(role_ids or ()), member__role__is_active=True)
            qs = qs.filter(Q(pk__in=list(user_ids or ())) | by_role)
        return qs.distinct()

    def role_ids_of(self, user):
        member = self._member(user)
        return {member.role_id} if member and member.role and member.role.is_active else set()

    def roles(self, tenant):
        return Role.objects.filter(tenant=tenant, is_active=True)

    def role_member_count(self, role):
        return Member.objects.filter(role=role, removed=False, user__is_active=True).count()

    def has_permission(self, user, codename):
        if user.is_superuser:
            return True
        member = self._member(user)
        return bool(member and member.role and member.role.is_active and codename in member.role.permissions)

    def role_has_permission(self, role, codename):
        return codename in role.permissions

    def is_enabled(self, tenant):
        return tenant.pk not in self.disabled_tenant_ids

    def scope_label(self, tenant, scope_key):
        return scope_key.replace("folder:", "Folder ")

    def notify(self, notice):
        self.outbox.append(notice)

    def audit(self, record):
        self.audit_log.append(record)

    def request_url(self, approval_request):
        return f"/approvals/?open={approval_request.reference}"
