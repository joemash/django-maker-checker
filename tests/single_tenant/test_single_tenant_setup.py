"""Single-tenant setup: maker-checker in a plain Django project, with no tenancy and no adapter to write.

Leave ``APPROVALS_TENANT_MODEL``, ``APPROVALS_ROLE_MODEL`` and ``APPROVALS_ADAPTER`` unset and the engine uses:

- one built-in tenant (``approvals.Tenant``, a single "default" row) that every active user belongs to;
- Django groups as roles, so a rule can name a group of approvers;
- Django permissions in the ``approvals`` app, created after every ``migrate``: ``approve_<action_type>`` for each
  registered action and ``manage_approval_policies`` for administrators;
- email notifications through ``django.core.mail``, and audit records on the ``django_maker_checker.audit`` logger.

This module runs under ``tests.settings_single`` (see ``tests/conftest.py``).
"""

from io import StringIO

from django.conf import settings
from django.contrib.auth.models import AnonymousUser, Group, Permission, User
from django.core import mail
from django.core.management import call_command
from django.test import TestCase

from django_maker_checker import conf, policies, services
from django_maker_checker.auth_adapter import AuthApprovalsAdapter
from django_maker_checker.constants import Decision, RequestStatus
from django_maker_checker.exceptions import ApprovalError
from django_maker_checker.models import Tenant
from django_maker_checker.policies import PolicySpec
from tests.single_tenant.actions import RENAME, rename_group


def grant(group, codename):
    group.permissions.add(Permission.objects.get(content_type__app_label="approvals", codename=codename))


class Defaults(TestCase):
    def test_the_built_in_tenant_group_roles_and_adapter_are_used(self):
        self.assertEqual(settings.APPROVALS_TENANT_MODEL, "approvals.Tenant")
        self.assertEqual(settings.APPROVALS_ROLE_MODEL, "auth.Group")
        self.assertIsInstance(conf.get_adapter(), AuthApprovalsAdapter)

    def test_there_is_one_tenant(self):
        self.assertEqual(Tenant.default(), Tenant.default())
        self.assertEqual(Tenant.objects.count(), 1)

    def test_every_active_user_belongs_to_it_and_nobody_else_does(self):
        adapter = conf.get_adapter()
        self.assertEqual(adapter.tenant_of(User.objects.create_user("ann")), Tenant.default())
        self.assertIsNone(adapter.tenant_of(User.objects.create_user("gone", is_active=False)))
        self.assertIsNone(adapter.tenant_of(AnonymousUser()))

    def test_migrate_creates_a_permission_per_action_and_one_for_administrators(self):
        codenames = set(
            Permission.objects.filter(content_type__app_label="approvals").values_list("codename", flat=True)
        )
        self.assertLessEqual({"approve_group_rename", "manage_approval_policies"}, codenames)

    def test_migrations_match_the_models(self):
        out = StringIO()
        call_command("makemigrations", "approvals", "--check", "--dry-run", stdout=out)
        self.assertIn("No changes detected", out.getvalue())


class MakerCheckerWithGroups(TestCase):
    def setUp(self):
        self.tenant = Tenant.default()
        self.reviewers = Group.objects.create(name="Reviewers")
        grant(self.reviewers, "approve_group_rename")
        self.maker = User.objects.create_user("maker", email="maker@example.com")
        self.checker = User.objects.create_user("checker", email="checker@example.com")
        self.checker.groups.add(self.reviewers)
        self.admin = User.objects.create_user("admin")
        self.team = Group.objects.create(name="Team")
        policies.save_policy(
            policies.policy_for(self.tenant, RENAME), PolicySpec(approvers=[f"r:{self.reviewers.pk}"]), by=self.admin
        )

    def rename(self, maker=None):
        with self.captureOnCommitCallbacks(execute=True):
            return rename_group(tenant=self.tenant, maker=maker or self.maker, group_id=self.team.pk, name="Platform")

    def test_a_rule_naming_a_group_holds_the_change_for_its_members(self):
        outcome = self.rename()
        self.assertTrue(outcome.pending)
        self.team.refresh_from_db()
        self.assertEqual(self.team.name, "Team")

    def test_a_group_member_with_the_permission_approves_and_the_change_applies(self):
        req = self.rename().approval_request
        self.assertEqual(services.decide(req.pk, self.checker, Decision.APPROVE).status, RequestStatus.EXECUTED)
        self.team.refresh_from_db()
        self.assertEqual(self.team.name, "Platform")

    def test_the_maker_still_cannot_approve_their_own_change(self):
        self.maker.groups.add(self.reviewers)
        req = self.rename().approval_request
        with self.assertRaisesMessage(ApprovalError, "request you raised"):
            services.decide(req.pk, self.maker, Decision.APPROVE)

    def test_approvers_without_the_permission_are_refused_when_the_rule_is_saved(self):
        others = Group.objects.create(name="Others")
        with self.assertRaisesMessage(policies.PolicyError, "not a valid approver"):
            policies.save_policy(
                policies.policy_for(self.tenant, RENAME), PolicySpec(approvers=[f"r:{others.pk}"]), by=self.admin
            )

    def test_approvers_are_emailed(self):
        self.rename()
        (email,) = mail.outbox
        self.assertEqual(email.to, ["checker@example.com"])
        self.assertIn("Rename Group", email.subject + email.body)

    def test_events_are_audited_to_the_logger(self):
        with self.assertLogs("django_maker_checker.audit", level="INFO") as logs:
            self.rename()
        self.assertTrue(any("created ApprovalRequest" in line for line in logs.output))
