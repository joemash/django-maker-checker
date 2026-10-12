"""Configuring rules: which actions need approval, by whom, and how fast.

A rule (``ApprovalPolicy``) belongs to a tenant and an action type, and is either tenant-wide (``scope_key=""``) or
for one host-defined scope (e.g. ``"folder:general"``). Host UIs build a ``PolicySpec`` from a form and call
``policies.save_policy``; all validation lives there so every UI enforces the same rules.

Workflows:
    SINGLE      one approver
    MULTI       several approvers in one step: any one of them, N of them (``required_approvals``), or all
                (``require_all``)
    SEQUENTIAL  an approval flow: one step per approver, in order

An SLA gives each step a time limit; overdue steps escalate to a backup approver.
"""

from django_maker_checker import policies, services
from django_maker_checker.constants import WorkflowType
from django_maker_checker.models import ApprovalPolicy, ApprovalPolicyApprover
from django_maker_checker.policies import PolicyError, PolicySpec
from tests.base import EngineTestCase
from tests.testapp.handlers import EDIT, FLAKY, MERGE, PUBLISH
from tests.testapp.models import Document


class SavingARule(EngineTestCase):
    def save(self, **spec):
        return policies.save_policy(policies.policy_for(self.tenant, PUBLISH), PolicySpec(**spec), by=self.admin)

    def test_an_unsaved_rule_starts_enabled_with_defaults(self):
        policy = policies.policy_for(self.tenant, PUBLISH)
        self.assertIsNone(policy.pk)
        self.assertTrue(policy.is_enabled)
        self.assertEqual(policy.workflow_type, WorkflowType.SINGLE)

    def test_approvers_are_tokens_u_for_users_and_r_for_roles(self):
        policy = self.save(
            workflow_type=WorkflowType.SEQUENTIAL,
            approvers=[f"r:{self.role_reviewer.pk}", f"u:{self.admin.pk}"],
            sla_enabled=True,
            sla_value=4,
            sla_unit="hours",
            escalation=f"u:{self.admin.pk}",
        )
        self.assertEqual(
            [a.approver_token for a in policy.ordered_approvers()], [f"r:{self.role_reviewer.pk}", f"u:{self.admin.pk}"]
        )
        self.assertEqual((policy.sla_minutes, policy.escalation_user), (240, self.admin))
        self.assertEqual(policy.created_by, self.admin)

    def test_require_all_only_applies_to_multi_approver_rules(self):
        policy = self.save(approvers=[f"u:{self.checker1.pk}"], require_all=True)
        self.assertFalse(policy.require_all)

    def test_duplicate_and_blank_approvers_are_ignored_outside_approval_flows(self):
        token = f"u:{self.checker1.pk}"
        policy = self.save(workflow_type=WorkflowType.MULTI, approvers=[token, "", token, f"u:{self.checker2.pk}"])
        self.assertEqual(policy.approvers.count(), 2)

    def test_a_multi_approver_rule_can_require_n_of_its_approvers(self):
        approvers = [f"u:{self.checker1.pk}", f"u:{self.checker2.pk}", f"u:{self.admin.pk}"]
        policy = self.save(workflow_type=WorkflowType.MULTI, approvers=approvers, required_approvals=2)
        self.assertEqual(policy.required_approvals, 2)
        self.assertEqual(policies.describe(policy)["Workflow"], "Multiple Approvers (2 of 3 must approve)")

    def test_repeating_a_role_asks_for_that_many_different_members_of_it(self):
        reviewer = f"r:{self.role_reviewer.pk}"
        policy = self.save(workflow_type=WorkflowType.MULTI, approvers=[reviewer, reviewer], require_all=True)
        self.assertEqual([a.approver_role for a in policy.ordered_approvers()], [self.role_reviewer] * 2)

    def test_switching_the_sla_off_keeps_its_value_for_when_it_is_switched_back_on(self):
        sla = {"sla_value": 4, "sla_unit": "hours", "escalation": f"u:{self.admin.pk}"}
        self.save(approvers=[f"u:{self.checker1.pk}"], sla_enabled=True, **sla)
        policy = self.save(approvers=[f"u:{self.checker1.pk}"], sla_enabled=False)
        self.assertEqual((policy.sla_enabled, policy.sla_value, policy.sla_unit), (False, 4, "hours"))


class RuleValidation(EngineTestCase):
    def save(self, **spec):
        return policies.save_policy(policies.policy_for(self.tenant, PUBLISH), PolicySpec(**spec), by=self.admin)

    def test_invalid_specs_are_refused_with_a_readable_reason(self):
        cases = [
            ({"workflow_type": "committee", "approvers": [f"u:{self.admin.pk}"]}, "Unknown workflow type"),
            ({"approvers": [f"u:{self.admin.pk}"], "risk_level": "extreme"}, "Unknown risk level"),
            ({"workflow_type": WorkflowType.SEQUENTIAL, "approvers": [f"u:{self.admin.pk}", ""]}, "Every step"),
            ({"workflow_type": WorkflowType.SINGLE, "approvers": []}, "exactly one"),
            ({"workflow_type": WorkflowType.MULTI, "approvers": [f"u:{self.admin.pk}"]}, "at least two"),
            ({"approvers": [f"u:{self.admin.pk}"], "sla_enabled": True, "sla_unit": "hours"}, "at least 1"),
            (
                {"approvers": [f"u:{self.checker1.pk}"], "sla_enabled": True, "sla_value": 2, "sla_unit": "hours"},
                "escalation backup",
            ),
        ]
        for spec, message in cases:
            with self.subTest(spec=spec), self.assertRaisesMessage(PolicyError, message):
                self.save(**spec)

    def test_required_approvals_must_fit_a_multi_approver_rule(self):
        two = [f"u:{self.checker1.pk}", f"u:{self.checker2.pk}"]
        cases = [
            ({"approvers": two[:1], "required_approvals": 1}, "only applies to Multiple Approvers"),
            ({"workflow_type": WorkflowType.MULTI, "approvers": two, "required_approvals": 3}, "between 1 and"),
            ({"workflow_type": WorkflowType.MULTI, "approvers": two, "required_approvals": 0}, "between 1 and"),
            (
                {"workflow_type": WorkflowType.MULTI, "approvers": two, "required_approvals": 2, "require_all": True},
                "not both",
            ),
        ]
        for spec, message in cases:
            with self.subTest(spec=spec), self.assertRaisesMessage(PolicyError, message):
                self.save(**spec)

    def test_approvers_must_belong_to_the_tenant_and_hold_the_approve_permission(self):
        cases = {
            "the maker's role lacks the permission": f"u:{self.maker.pk}",
            "a role of another tenant": f"r:{self.other_role.pk}",
            "a user of another tenant": f"u:{self.outsider.pk}",
            "a user that doesn't exist": "u:999999",
        }
        for case, token in cases.items():
            with self.subTest(case), self.assertRaisesMessage(PolicyError, "not a valid approver"):
                self.save(approvers=[token])

    def test_all_shape_errors_are_reported_together(self):
        with self.assertRaises(PolicyError) as caught:
            self.save(workflow_type=WorkflowType.MULTI, approvers=[], sla_enabled=True)
        self.assertEqual(len(caught.exception.messages), 4)


class WhichRuleApplies(EngineTestCase):
    payload = property(lambda self: {"document_id": self.document.pk})

    def test_no_rule_means_no_approval(self):
        self.assertFalse(services.requires_approval(self.tenant, PUBLISH, self.payload))

    def test_disabled_rules_and_rules_without_approvers_never_hold_an_action(self):
        self.policy(PUBLISH, [self.role_reviewer], is_enabled=False)
        self.assertFalse(services.requires_approval(self.tenant, PUBLISH, self.payload))
        self.policy(FLAKY, [])
        self.assertFalse(services.requires_approval(self.tenant, FLAKY, {"n": 1}))

    def test_rules_of_another_tenant_do_not_apply(self):
        theirs = ApprovalPolicy.objects.create(tenant=self.other_tenant, action_type=PUBLISH)
        ApprovalPolicyApprover.objects.create(policy=theirs, order=0, approver_role=self.other_role)
        self.assertFalse(services.requires_approval(self.tenant, PUBLISH, self.payload))

    def test_a_scoped_rule_wins_over_the_tenant_wide_rule_for_items_in_its_scope(self):
        self.policy(PUBLISH, [self.checker1])
        scoped = self.policy(PUBLISH, [self.checker2], scope_key="folder:general")
        self.assertEqual(services.find_policy(self.tenant, PUBLISH, self.payload), scoped)

        self.document.folder = "archive"
        self.document.save()
        self.assertEqual(services.find_policy(self.tenant, PUBLISH, self.payload).scope_key, "")

    def test_scoped_rules_can_only_be_added_for_actions_that_support_scopes(self):
        policy = policies.add_scoped_policy(self.tenant, PUBLISH, "folder:general", by=self.admin)
        self.assertEqual(policy.scope_key, "folder:general")
        with self.assertRaisesMessage(PolicyError, "cannot have per-scope rules"):
            policies.add_scoped_policy(self.tenant, EDIT, "folder:general", by=self.admin)


class ActionSpecificConditions(EngineTestCase):
    """A rule's ``conditions`` hold settings only its action understands (thresholds, scopes...). The action checks
    them (``ActionHandler.validate_conditions``) and uses them to decide which changes the rule holds (``matches``).
    """

    def save(self, conditions, **spec):
        spec.setdefault("approvers", [f"r:{self.role_reviewer.pk}"])
        return policies.save_policy(
            policies.policy_for(self.tenant, MERGE), PolicySpec(conditions=conditions, **spec), by=self.admin
        )

    def test_the_action_rejects_conditions_it_cant_use(self):
        with self.assertRaisesMessage(PolicyError, "at least 2"):
            self.save({"min_documents": 1})

    def test_its_errors_are_reported_with_the_rules_other_errors(self):
        with self.assertRaises(PolicyError) as caught:
            self.save({"min_documents": "three"}, approvers=[])
        self.assertEqual(len(caught.exception.messages), 2)

    def test_the_conditions_decide_which_changes_the_rule_holds(self):
        self.save({"min_documents": 3})
        second = Document.objects.create(tenant=self.tenant, title="Q3 appendix")
        third = Document.objects.create(tenant=self.tenant, title="Q3 notes")
        small = {"document_ids": [self.document.pk, second.pk], "title": "x"}
        large = {"document_ids": [self.document.pk, second.pk, third.pk], "title": "x"}
        self.assertFalse(services.requires_approval(self.tenant, MERGE, small))
        self.assertTrue(services.requires_approval(self.tenant, MERGE, large))
