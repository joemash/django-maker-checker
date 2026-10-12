from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from django_maker_checker.constants import WorkflowType
from django_maker_checker.models import ApprovalPolicy, ApprovalPolicyApprover
from django_maker_checker.permissions import MANAGE_POLICIES
from django_maker_checker.registry import all_handlers
from tests.testapp.adapter import TestAdapter
from tests.testapp.handlers import publish_document
from tests.testapp.models import Document, Member, Role, Tenant

APPROVE_ALL = [h.approve_permission for h in all_handlers()]


class EngineTestCase(TestCase):
    """The cast every specification uses.

    Tenant "Acme":
        maker     (Operator role: may raise changes, may approve nothing)
        checker1  (Reviewer role: may approve every action)
        checker2  (Reviewer role)
        admin     (Admin role: may approve every action and manage policies)
    Tenant "Other":
        outsider  (Reviewer role there: a valid approver, but for the wrong tenant)

    ``document`` is the record most actions change. Policies are created directly with ``policy()``; the policy
    service and its validation have their own specification in ``test_configuring_rules``.
    """

    def setUp(self):
        TestAdapter.outbox.clear()
        TestAdapter.audit_log.clear()
        TestAdapter.disabled_tenant_ids.clear()
        self.tenant = Tenant.objects.create(name="Acme")
        self.other_tenant = Tenant.objects.create(name="Other")
        self.role_operator = Role.objects.create(tenant=self.tenant, name="Operator")
        self.role_reviewer = Role.objects.create(tenant=self.tenant, name="Reviewer", permissions=APPROVE_ALL)
        self.role_admin = Role.objects.create(
            tenant=self.tenant, name="Admin", permissions=[*APPROVE_ALL, MANAGE_POLICIES]
        )
        self.other_role = Role.objects.create(tenant=self.other_tenant, name="Reviewer", permissions=APPROVE_ALL)

        self.maker = self.member("maker", self.role_operator)
        self.checker1 = self.member("checker1", self.role_reviewer)
        self.checker2 = self.member("checker2", self.role_reviewer)
        self.admin = self.member("admin", self.role_admin)
        self.outsider = self.member("outsider", self.other_role, tenant=self.other_tenant)
        self.document = Document.objects.create(tenant=self.tenant, title="Q3 report", amount=Decimal("10.00"))

    def member(self, username, role, tenant=None):
        user = get_user_model().objects.create_user(
            username=username, email=f"{username}@example.com", first_name=username.title(), last_name="Test"
        )
        Member.objects.create(user=user, tenant=tenant or self.tenant, role=role)
        return user

    def policy(self, action_type, approvers, workflow=WorkflowType.SINGLE, require_all=False, scope_key="", **fields):
        policy = ApprovalPolicy.objects.create(
            tenant=self.tenant,
            action_type=action_type,
            workflow_type=workflow,
            require_all=require_all,
            scope_key=scope_key,
            **fields,
        )
        for order, approver in enumerate(approvers):
            kwargs = {"approver_role": approver} if isinstance(approver, Role) else {"approver_user": approver}
            ApprovalPolicyApprover.objects.create(policy=policy, order=order, **kwargs)
        return policy

    def publish(self, maker=None, **kwargs):
        """Ask to publish ``document``: pending when a rule applies, applied at once otherwise."""
        return publish_document(tenant=self.tenant, maker=maker or self.maker, document_id=self.document.pk, **kwargs)

    def raise_request(self, **kwargs):
        """Submit a publish that a rule holds for approval, and return the pending request."""
        outcome = self.publish(**kwargs)
        assert outcome.pending, "no rule applies; create one with self.policy() first"
        return outcome.approval_request
