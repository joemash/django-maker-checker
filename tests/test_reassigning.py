"""Reassigning: when an approver leaves, their open requests go to someone who can still approve them.

A request keeps the approvers it was raised with, so a step naming someone who has been deactivated could never be
approved. ``services.reassign_approver(user, by=...)``, called by the host when a user loses access, hands each of
their unfilled slots to the approver the rule names in the same position now, or else to the request's escalation
backup. Nobody suitable means the slot stays and the timeline says so.
"""

from django_maker_checker import services
from django_maker_checker.constants import Decision, EventType, RequestStatus, SlaUnit, WorkflowType
from django_maker_checker.models import ApprovalPolicyApprover
from tests.base import EngineTestCase
from tests.testapp.adapter import TestAdapter
from tests.testapp.handlers import PUBLISH


class ReassigningALeaver(EngineTestCase):
    def leave(self, user):
        user.is_active = False
        user.save(update_fields=["is_active"])
        with self.captureOnCommitCallbacks(execute=True):
            return services.reassign_approver(user, by=self.admin)

    def rename_approver(self, policy, old, new):
        ApprovalPolicyApprover.objects.filter(policy=policy, approver_user=old).update(approver_user=new)

    def reassigned_messages(self, req):
        return list(req.events.filter(type=EventType.REASSIGNED).values_list("message", flat=True))

    def test_the_slot_goes_to_the_approver_the_rule_names_now(self):
        policy = self.policy(PUBLISH, [self.checker1])
        req = self.raise_request()
        self.rename_approver(policy, self.checker1, self.checker2)
        self.assertEqual(self.leave(self.checker1), {"reassigned": 1, "unassigned": 0})
        self.assertEqual(
            self.reassigned_messages(req), ["Checker1 Test can no longer approve step 1; reassigned to Checker2 Test."]
        )
        self.assertEqual(TestAdapter.outbox[-1].recipients, (self.checker2,))
        self.assertEqual(services.decide(req.pk, self.checker2, Decision.APPROVE).status, RequestStatus.EXECUTED)

    def test_without_a_new_approver_in_the_rule_it_goes_to_the_escalation_backup(self):
        self.policy(
            PUBLISH, [self.checker1], sla_enabled=True, sla_value=4, sla_unit=SlaUnit.HOURS, escalation_user=self.admin
        )
        req = self.raise_request()
        self.leave(self.checker1)
        self.assertEqual(services.decide(req.pk, self.admin, Decision.APPROVE).status, RequestStatus.EXECUTED)

    def test_a_backup_kept_by_a_rule_whose_sla_is_off_does_not_take_over(self):
        # Switching a rule's SLA off keeps its last backup (so switching it back on restores it), but the backup
        # only counts while the SLA is on, as the rule's history shows.
        self.policy(PUBLISH, [self.checker1], sla_enabled=False, escalation_user=self.admin)
        req = self.raise_request()
        self.assertEqual(self.leave(self.checker1), {"reassigned": 0, "unassigned": 1})
        self.assertEqual(req.active_stage.approvers.get().approver_user, self.checker1)

    def test_with_nobody_to_take_over_the_slot_stays_and_the_timeline_says_so(self):
        self.policy(PUBLISH, [self.checker1])
        req = self.raise_request()
        self.assertEqual(self.leave(self.checker1), {"reassigned": 0, "unassigned": 1})
        self.assertIn("no replacement approver is configured", self.reassigned_messages(req)[0])
        self.assertEqual(req.active_stage.approvers.get().approver_user, self.checker1)

    def test_a_later_step_is_reassigned_without_notifying_anyone_yet(self):
        policy = self.policy(PUBLISH, [self.checker1, self.checker2], workflow=WorkflowType.SEQUENTIAL)
        req = self.raise_request()
        self.rename_approver(policy, self.checker2, self.admin)
        sent = len(TestAdapter.outbox)
        self.leave(self.checker2)
        self.assertEqual(len(TestAdapter.outbox), sent)
        self.assertEqual(req.stages.get(order=1).approvers.get().approver_user, self.admin)

    def test_nobody_already_named_on_the_request_takes_over(self):
        policy = self.policy(PUBLISH, [self.checker1, self.checker2], workflow=WorkflowType.SEQUENTIAL)
        self.raise_request()
        self.rename_approver(policy, self.checker2, self.checker1)
        self.assertEqual(self.leave(self.checker2), {"reassigned": 0, "unassigned": 1})

    def test_the_maker_never_takes_over(self):
        policy = self.policy(PUBLISH, [self.checker1])
        self.raise_request()
        self.rename_approver(policy, self.checker1, self.maker)
        self.assertEqual(self.leave(self.checker1), {"reassigned": 0, "unassigned": 1})

    def test_role_slots_and_finished_requests_are_left_alone(self):
        self.policy(PUBLISH, [self.role_reviewer])
        req = self.raise_request()
        self.assertEqual(self.leave(self.checker1), {"reassigned": 0, "unassigned": 0})
        services.decide(req.pk, self.checker2, Decision.APPROVE)
        self.assertEqual(self.reassigned_messages(req), [])
