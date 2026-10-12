"""Requesting changes: an approver can send a request back to its maker instead of rejecting it outright.

``services.decide(..., Decision.REQUEST_CHANGES, note)`` moves a pending request to CHANGES_REQUESTED, with a note
saying what to change. While it is with the maker, nobody can decide it, its SLA is paused, and it keeps its lock on
the item. The maker then either withdraws it (``cancel``) or calls ``services.resubmit``, optionally with an amended
payload for the same item. Resubmission starts a new round: approval restarts from the first step, and approvals
from the earlier round no longer count, because they approved a different change.

    PENDING ──request changes──▶ CHANGES_REQUESTED ──resubmit──▶ PENDING (round 2)
                                        └──cancel──▶ CANCELLED
"""

from datetime import timedelta

from django_maker_checker import queries, services
from django_maker_checker.constants import Decision, EventType, RequestStatus, SlaUnit, WorkflowType
from django_maker_checker.exceptions import ApprovalError
from tests.base import EngineTestCase
from tests.testapp.adapter import TestAdapter
from tests.testapp.handlers import PUBLISH
from tests.testapp.models import Document


class SendingARequestBack(EngineTestCase):
    def setUp(self):
        super().setUp()
        self.policy(PUBLISH, [self.role_reviewer], sla_enabled=True, sla_value=4, sla_unit=SlaUnit.HOURS)
        self.req = self.raise_request(note="draft")

    def send_back(self, note="Attach the auditor's letter"):
        with self.captureOnCommitCallbacks(execute=True):
            return services.decide(self.req.pk, self.checker1, Decision.REQUEST_CHANGES, note)

    def test_a_current_approver_sends_it_back_with_a_note_and_the_maker_is_told(self):
        req = self.send_back()
        self.assertEqual(req.status, RequestStatus.CHANGES_REQUESTED)
        self.assertTrue(req.events.filter(type=EventType.CHANGES_REQUESTED).exists())
        notice = TestAdapter.outbox[-1]
        self.assertEqual((notice.kind, notice.recipients), ("changes_requested", (self.maker,)))
        self.assertIn("Attach the auditor's letter", notice.text)

    def test_a_note_is_required(self):
        with self.assertRaisesMessage(ApprovalError, "Say what needs to change"):
            services.decide(self.req.pk, self.checker1, Decision.REQUEST_CHANGES, " ")

    def test_the_maker_cannot_send_their_own_request_back(self):
        with self.assertRaisesMessage(ApprovalError, "request you raised"):
            services.decide(self.req.pk, self.maker, Decision.REQUEST_CHANGES, "Not ready")

    def test_while_it_is_with_the_maker_nobody_can_decide_it(self):
        self.send_back()
        with self.assertRaisesMessage(ApprovalError, "sent back to its maker"):
            services.decide(self.req.pk, self.checker2, Decision.APPROVE)

    def test_it_keeps_the_item_locked(self):
        self.send_back()
        with self.assertRaisesMessage(ApprovalError, self.req.reference):
            self.publish(maker=self.admin)

    def test_its_sla_is_paused(self):
        self.send_back()
        later = self.req.stages.get(order=0).started_at + timedelta(hours=5)
        self.assertEqual(services.process_sla(later), {"warned": 0, "escalated": 0})

    def test_the_maker_may_resubmit_or_withdraw_it(self):
        req = self.send_back()
        self.assertEqual(
            queries.allowed_actions(self.maker, req),
            queries.AllowedActions(cancel=True, resubmit=True, is_maker=True),
        )
        self.assertEqual(services.cancel(self.req.pk, self.maker).status, RequestStatus.CANCELLED)


class Resubmitting(EngineTestCase):
    def setUp(self):
        super().setUp()
        self.policy(PUBLISH, [self.checker1, self.checker2], workflow=WorkflowType.MULTI, require_all=True)
        self.req = self.raise_request(note="draft")
        services.decide(self.req.pk, self.checker1, Decision.APPROVE)
        services.decide(self.req.pk, self.checker2, Decision.REQUEST_CHANGES, "Use the final figures")

    def test_resubmitting_starts_a_new_round_and_notifies_the_approvers(self):
        with self.captureOnCommitCallbacks(execute=True):
            req = services.resubmit(self.req.pk, self.maker, justification="Final figures attached")
        self.assertEqual((req.status, req.submission, req.justification), ("pending", 2, "Final figures attached"))
        self.assertTrue(req.events.filter(type=EventType.RESUBMITTED).exists())
        self.assertEqual(TestAdapter.outbox[-1].kind, "requested")

    def test_earlier_approvals_no_longer_count_and_everyone_may_decide_again(self):
        req = services.resubmit(self.req.pk, self.maker)
        req = services.decide(req.pk, self.checker2, Decision.APPROVE)
        self.assertEqual(req.status, RequestStatus.PENDING)  # checker1's approval was for the earlier version
        self.assertEqual(services.decide(req.pk, self.checker1, Decision.APPROVE).status, RequestStatus.EXECUTED)

    def test_the_change_can_be_amended_and_the_amended_version_is_applied(self):
        req = services.resubmit(self.req.pk, self.maker, payload={"document_id": self.document.pk, "note": "final"})
        self.assertEqual(req.payload["note"], "final")
        services.decide(req.pk, self.checker1, Decision.APPROVE)
        req = services.decide(req.pk, self.checker2, Decision.APPROVE)
        self.assertEqual(req.result["note"], "final")

    def test_an_amended_change_must_be_for_the_same_item(self):
        other = Document.objects.create(tenant=self.tenant, title="Q4 report")
        with self.assertRaisesMessage(ApprovalError, "same item"):
            services.resubmit(self.req.pk, self.maker, payload={"document_id": other.pk})

    def test_only_the_maker_resubmits_and_only_a_request_sent_back(self):
        with self.assertRaisesMessage(ApprovalError, "Only the maker"):
            services.resubmit(self.req.pk, self.checker1)
        services.resubmit(self.req.pk, self.maker)
        with self.assertRaisesMessage(ApprovalError, "Only the maker"):
            services.resubmit(self.req.pk, self.maker)


class InAnApprovalFlow(EngineTestCase):
    def test_changes_requested_at_a_later_step_restart_approval_from_the_first_step(self):
        self.policy(PUBLISH, [self.checker1, self.admin], workflow=WorkflowType.SEQUENTIAL)
        req = services.decide(self.raise_request().pk, self.checker1, Decision.APPROVE)
        services.decide(req.pk, self.admin, Decision.REQUEST_CHANGES, "Needs legal sign-off")
        req = services.resubmit(req.pk, self.maker)
        self.assertEqual(req.current_stage, 0)
        with self.assertRaisesMessage(ApprovalError, "not an approver for the current step"):
            services.decide(req.pk, self.admin, Decision.APPROVE)
        self.assertEqual(services.decide(req.pk, self.checker1, Decision.APPROVE).current_stage, 1)
