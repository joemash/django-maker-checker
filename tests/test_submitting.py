"""Submitting: what happens when a maker asks for a change that a rule holds for approval.

The change is not applied. Instead an ``ApprovalRequest`` is raised (status PENDING), carrying everything needed
to review and later apply it: the payload, a "before" snapshot, the maker's justification, and a snapshot of the
rule, so later edits to the rule never change a request already in flight. The request's stages are copied from the
rule's approvers and only the first stage starts.
"""

from datetime import timedelta

from django_fsm import TransitionNotAllowed

from django_maker_checker import services
from django_maker_checker.constants import EventType, RequestStatus, SlaUnit, WorkflowType
from django_maker_checker.exceptions import ApprovalError
from tests.base import EngineTestCase
from tests.testapp.adapter import TestAdapter
from tests.testapp.handlers import PUBLISH


class RaisingARequest(EngineTestCase):
    def setUp(self):
        super().setUp()
        self.policy(PUBLISH, [self.role_reviewer], risk_level="high")

    def test_the_change_is_held_and_nothing_is_applied(self):
        req = self.raise_request(justification="  Board approved  ")
        self.assertEqual(req.status, RequestStatus.PENDING)
        self.assertEqual(req.justification, "Board approved")
        self.document.refresh_from_db()
        self.assertFalse(self.document.published)

    def test_the_request_gets_a_readable_reference_and_the_rules_risk_level(self):
        req = self.raise_request()
        self.assertTrue(req.reference.startswith("REQ-"))
        self.assertEqual(req.risk_level, "high")

    def test_the_items_current_state_is_snapshotted_for_review(self):
        self.assertEqual(self.raise_request().before_snapshot, {"published": False})

    def test_submission_is_on_the_timeline_and_in_the_host_audit_log(self):
        req = self.raise_request()
        self.assertEqual(req.events.get().type, EventType.CREATED)
        self.assertEqual(req.events.get().message, "Maker Test submitted Publish Document for Q3 report.")
        record = TestAdapter.audit_log[-1]
        self.assertEqual((record.resource_type, record.action), ("ApprovalRequest", "created"))

    def test_the_current_approvers_are_notified_once_the_transaction_commits(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.raise_request()
        notice = TestAdapter.outbox[-1]
        self.assertEqual(notice.kind, "requested")
        self.assertEqual({u.username for u in notice.recipients}, {"checker1", "checker2"})

    def test_requests_know_which_version_of_the_rule_they_were_raised_under(self):
        # Rules created outside policies.save_policy have no revisions, so no version is recorded.
        self.assertIsNone(self.raise_request().policy_version)


class StagesFollowTheWorkflow(EngineTestCase):
    def stages(self, approvers, workflow, **fields):
        self.policy(PUBLISH, approvers, workflow=workflow, **fields)
        return list(self.raise_request().stages.all())

    def test_single_approver_rules_have_one_stage_with_one_slot(self):
        (stage,) = self.stages([self.checker1], WorkflowType.SINGLE)
        self.assertEqual(stage.approvers.count(), 1)

    def test_multi_approver_rules_put_every_approver_in_one_stage(self):
        (stage,) = self.stages([self.checker1, self.checker2], WorkflowType.MULTI, require_all=True)
        self.assertEqual((stage.approvers.count(), stage.require_all), (2, True))

    def test_approval_flows_have_one_stage_per_step_and_only_the_first_starts(self):
        first, second = self.stages(
            [self.role_reviewer, self.admin],
            WorkflowType.SEQUENTIAL,
            sla_enabled=True,
            sla_value=2,
            sla_unit=SlaUnit.HOURS,
        )
        self.assertIsNotNone(first.started_at)
        self.assertEqual(first.due_at - first.started_at, timedelta(hours=2))
        self.assertEqual((second.started_at, second.due_at), (None, None))


class SubmissionIsRefused(EngineTestCase):
    def test_when_no_one_but_the_maker_could_approve(self):
        self.policy(PUBLISH, [self.maker])
        with self.assertRaisesMessage(ApprovalError, "No eligible approver other than you"):
            self.publish()

    def test_when_a_later_step_of_an_approval_flow_has_no_one_but_the_maker(self):
        self.policy(PUBLISH, [self.checker1, self.maker], workflow=WorkflowType.SEQUENTIAL)
        with self.assertRaisesMessage(ApprovalError, "for step 2"):
            self.publish()

    def test_while_another_request_for_the_same_item_is_open_naming_that_request(self):
        self.policy(PUBLISH, [self.role_reviewer])
        first = self.raise_request()
        with self.assertRaisesMessage(ApprovalError, f"already an open approval request ({first.reference})"):
            self.publish(maker=self.admin)

    def test_when_submitting_directly_without_any_rule(self):
        with self.assertRaisesMessage(ApprovalError, "No approval policy applies"):
            services.submit(PUBLISH, tenant=self.tenant, maker=self.maker, payload={"document_id": self.document.pk})


class StatusOnlyChangesThroughTheServices(EngineTestCase):
    def test_status_cannot_be_assigned_or_transitioned_out_of_order(self):
        self.policy(PUBLISH, [self.role_reviewer])
        req = self.raise_request()
        with self.assertRaises(AttributeError):
            req.status = RequestStatus.EXECUTED
        with self.assertRaises(TransitionNotAllowed):
            req.mark_executed()
