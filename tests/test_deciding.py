"""Deciding: approving and rejecting, and the segregation-of-duties rules around who may do either.

``services.decide(request_id, user, decision, comment)`` is the single entry point. A user may decide only when they
hold an open slot in the request's *current* stage (directly, through their role, or as a delegate) and are not
the maker. Approving the last required slot of the last stage executes the change as the maker's; executing failures
leave the decision in place and mark the request FAILED so it can be retried.

Life cycle:  PENDING ──approve──▶ APPROVED ──execute──▶ EXECUTED
                │                             └─fails──▶ FAILED ──retry──▶ EXECUTED
                ├──reject──▶ REJECTED
                └──cancel──▶ CANCELLED
"""

from django.test import override_settings

from django_maker_checker import services
from django_maker_checker.constants import Decision, EventType, RequestStatus, SlaUnit, WorkflowType
from django_maker_checker.exceptions import ApprovalError
from tests.base import EngineTestCase
from tests.testapp.adapter import TestAdapter
from tests.testapp.handlers import FLAKY, PUBLISH, FlakyHandler


class Approving(EngineTestCase):
    def test_approval_applies_the_change_as_the_makers(self):
        self.policy(PUBLISH, [self.role_reviewer])
        with self.captureOnCommitCallbacks(execute=True):
            req = services.decide(self.raise_request().pk, self.checker1, Decision.APPROVE, "Looks right")
        self.assertEqual(req.status, RequestStatus.EXECUTED)
        self.assertEqual((req.result["by"], req.decided_by), ("maker", self.checker1))
        self.document.refresh_from_db()
        self.assertTrue(self.document.published)
        self.assertEqual(TestAdapter.outbox[-1].kind, "decided")

    def test_the_approvers_comment_goes_on_the_timeline(self):
        self.policy(PUBLISH, [self.role_reviewer])
        req = services.decide(self.raise_request().pk, self.checker1, Decision.APPROVE, "Looks right")
        self.assertIn("Looks right", req.events.filter(type=EventType.COMMENT).values_list("message", flat=True))

    def test_a_request_can_only_be_decided_once(self):
        self.policy(PUBLISH, [self.role_reviewer])
        req = self.raise_request()
        services.decide(req.pk, self.checker1, Decision.APPROVE)
        with self.assertRaisesMessage(ApprovalError, "already approved & executed"):
            services.decide(req.pk, self.checker2, Decision.APPROVE)


class Rejecting(EngineTestCase):
    def setUp(self):
        super().setUp()
        self.policy(PUBLISH, [self.role_reviewer])
        self.req = self.raise_request()

    def test_a_rejection_needs_a_reason(self):
        with self.assertRaisesMessage(ApprovalError, "reason is required"):
            services.decide(self.req.pk, self.checker1, Decision.REJECT, "  ")

    @override_settings(APPROVALS_COMMENT_MAX_LENGTH=20)
    def test_notes_longer_than_the_limit_are_refused_and_nothing_is_recorded(self):
        for decision in (Decision.REJECT, Decision.REQUEST_CHANGES, Decision.APPROVE):
            with self.subTest(decision=decision), self.assertRaisesMessage(ApprovalError, "at most 20 characters"):
                services.decide(self.req.pk, self.checker1, decision, "x" * 21)
        self.assertFalse(self.req.decisions.exists())
        self.assertEqual(
            services.decide(self.req.pk, self.checker1, Decision.REJECT, "x" * 20).rejection_reason, "x" * 20
        )

    def test_rejecting_ends_the_request_and_frees_the_item_for_a_new_request(self):
        req = services.decide(self.req.pk, self.checker1, Decision.REJECT, "Wrong figures")
        self.assertEqual((req.status, req.rejection_reason), (RequestStatus.REJECTED, "Wrong figures"))
        self.assertTrue(self.publish().pending)


class WhoMayDecide(EngineTestCase):
    """Segregation of duties: nobody approves their own change, and only the current step's approvers decide."""

    def test_the_maker_never_approves_their_own_request_even_as_superuser_in_an_approver_role(self):
        self.maker.is_superuser = True
        self.maker.save()
        self.policy(PUBLISH, [self.role_operator, self.role_reviewer], workflow=WorkflowType.MULTI)
        with self.assertRaisesMessage(ApprovalError, "request you raised"):
            services.decide(self.raise_request().pk, self.maker, Decision.APPROVE)

    def test_a_user_not_named_in_the_stage_cannot_decide(self):
        self.policy(PUBLISH, [self.checker1])
        with self.assertRaisesMessage(ApprovalError, "not an approver"):
            services.decide(self.raise_request().pk, self.checker2, Decision.APPROVE)

    def test_a_member_of_another_tenant_cannot_decide(self):
        self.policy(PUBLISH, [self.role_reviewer])
        with self.assertRaises(ApprovalError):
            services.decide(self.raise_request().pk, self.outsider, Decision.APPROVE)

    def test_a_deactivated_role_grants_nothing(self):
        self.policy(PUBLISH, [self.role_reviewer])
        req = self.raise_request()
        self.role_reviewer.is_active = False
        self.role_reviewer.save()
        with self.assertRaises(ApprovalError):
            services.decide(req.pk, self.checker1, Decision.APPROVE)

    def test_editing_the_rule_does_not_change_who_decides_a_request_in_flight(self):
        self.policy(PUBLISH, [self.checker1])
        req = self.raise_request()
        req.policy.approvers.update(approver_user=self.checker2)
        with self.assertRaises(ApprovalError):
            services.decide(req.pk, self.checker2, Decision.APPROVE)
        self.assertEqual(services.decide(req.pk, self.checker1, Decision.APPROVE).status, RequestStatus.EXECUTED)


class MultiApproverRules(EngineTestCase):
    def test_any_one_approver_is_enough_by_default(self):
        self.policy(PUBLISH, [self.checker1, self.checker2], workflow=WorkflowType.MULTI)
        req = services.decide(self.raise_request().pk, self.checker2, Decision.APPROVE)
        self.assertEqual(req.status, RequestStatus.EXECUTED)

    def test_with_require_all_every_approver_must_approve_and_each_only_once(self):
        self.policy(PUBLISH, [self.checker1, self.checker2], workflow=WorkflowType.MULTI, require_all=True)
        req = services.decide(self.raise_request().pk, self.checker1, Decision.APPROVE)
        self.assertEqual(req.status, RequestStatus.PENDING)
        self.assertIn("(1 of 2 required approvals)", req.events.last().message)
        with self.assertRaisesMessage(ApprovalError, "already acted"):
            services.decide(req.pk, self.checker1, Decision.APPROVE)
        self.assertEqual(services.decide(req.pk, self.checker2, Decision.APPROVE).status, RequestStatus.EXECUTED)


class NOfMApprovals(EngineTestCase):
    """A Multiple Approvers rule can require any N of its approvers; a role listed twice needs two of its members."""

    def test_the_change_applies_once_n_of_the_approvers_have_approved(self):
        self.policy(
            PUBLISH, [self.checker1, self.checker2, self.admin], workflow=WorkflowType.MULTI, required_approvals=2
        )
        req = services.decide(self.raise_request().pk, self.admin, Decision.APPROVE)
        self.assertEqual(req.status, RequestStatus.PENDING)
        self.assertIn("(1 of 2 required approvals)", req.events.last().message)
        self.assertEqual(services.decide(req.pk, self.checker2, Decision.APPROVE).status, RequestStatus.EXECUTED)

    def test_a_role_listed_twice_needs_two_different_members(self):
        self.policy(PUBLISH, [self.role_reviewer, self.role_reviewer], workflow=WorkflowType.MULTI, require_all=True)
        req = services.decide(self.raise_request().pk, self.checker1, Decision.APPROVE)
        with self.assertRaisesMessage(ApprovalError, "already acted"):
            services.decide(req.pk, self.checker1, Decision.APPROVE)
        self.assertEqual(services.decide(req.pk, self.checker2, Decision.APPROVE).status, RequestStatus.EXECUTED)


class ApprovalFlows(EngineTestCase):
    def setUp(self):
        super().setUp()
        self.policy(
            PUBLISH,
            [self.role_reviewer, self.admin],
            workflow=WorkflowType.SEQUENTIAL,
            sla_enabled=True,
            sla_value=2,
            sla_unit=SlaUnit.HOURS,
        )
        self.req = self.raise_request()

    def test_later_steps_cannot_decide_before_their_turn(self):
        with self.assertRaisesMessage(ApprovalError, "not an approver for the current step"):
            services.decide(self.req.pk, self.admin, Decision.APPROVE)

    def test_approving_a_step_routes_to_the_next_with_a_fresh_sla_window(self):
        req = services.decide(self.req.pk, self.checker1, Decision.APPROVE)
        self.assertEqual((req.status, req.current_stage), (RequestStatus.PENDING, 1))
        self.assertIsNotNone(req.active_stage.due_at)
        self.assertEqual(req.events.last().message, "Step 1 approved by Checker1 Test; routed to step 2.")

    def test_one_person_cannot_approve_two_steps(self):
        req = services.decide(self.req.pk, self.checker1, Decision.APPROVE)
        with self.assertRaises(ApprovalError):
            services.decide(req.pk, self.checker1, Decision.APPROVE)

    def test_the_last_step_executes_the_change(self):
        services.decide(self.req.pk, self.checker1, Decision.APPROVE)
        self.assertEqual(services.decide(self.req.pk, self.admin, Decision.APPROVE).status, RequestStatus.EXECUTED)

    def test_a_rejection_at_any_step_ends_the_whole_request(self):
        services.decide(self.req.pk, self.checker1, Decision.APPROVE)
        req = services.decide(self.req.pk, self.admin, Decision.REJECT, "Not this quarter")
        self.assertEqual(req.status, RequestStatus.REJECTED)


class ExecutionFailures(EngineTestCase):
    def setUp(self):
        super().setUp()
        self.policy(FLAKY, [self.role_reviewer])
        req = services.submit(FLAKY, tenant=self.tenant, maker=self.maker, payload={"n": 1})
        self.req = services.decide(req.pk, self.checker1, Decision.APPROVE)

    def test_a_failure_keeps_the_approval_and_records_the_error(self):
        self.assertEqual(self.req.status, RequestStatus.FAILED)
        self.assertEqual(self.req.decided_by, self.checker1)
        self.assertIn("downstream unavailable", self.req.execution_error)

    def test_only_an_administrator_may_retry(self):
        with self.assertRaises(ApprovalError):
            services.retry(self.req.pk, self.checker1)
        FlakyHandler.fail = False
        self.addCleanup(setattr, FlakyHandler, "fail", True)
        self.assertEqual(services.retry(self.req.pk, self.admin).status, RequestStatus.EXECUTED)
