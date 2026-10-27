"""Read replicas: decisions work when the host sends reads to a replica.

Many hosts route plain reads to a read replica and only writes to the primary. A decision fills a slot and then reads
the stage back to see whether it is complete, inside one transaction; a replica can't see that uncommitted write, so
reading it from there would leave every approval one short ("0 of 1 required approvals"). Rows reached through a
request locked for a decision are read from the database it came from, so routing reads elsewhere changes nothing.

``StaleReplicaRouter`` sends reads of request rows to a replica that never catches up: the worst case, so anything the
engine reads from it in a write path is missing.
"""

from datetime import timedelta

from django.test import override_settings

from django_maker_checker import services
from django_maker_checker.constants import Decision, RequestStatus, SlaUnit, WorkflowType
from tests.base import EngineTestCase
from tests.testapp.adapter import TestAdapter
from tests.testapp.handlers import PUBLISH

REQUEST_ROWS = {
    "approvalrequest",
    "approvalstage",
    "approvalstageapprover",
    "approvaldecision",
    "approvalevent",
    "approvallock",
}


class StaleReplicaRouter:
    """Reads of request rows go to ``replica``; it ignores instance hints, as simple primary/replica routers do."""

    def db_for_read(self, model, **hints):
        if model._meta.app_label == "approvals" and model._meta.model_name in REQUEST_ROWS:
            return "replica"
        return None

    def allow_relation(self, obj1, obj2, **hints):
        return True


@override_settings(DATABASE_ROUTERS=["tests.test_read_replicas.StaleReplicaRouter"])
class DecidingWithAReadReplica(EngineTestCase):
    databases = {"default", "replica"}

    def approve(self, req, user):
        return services.decide(req.pk, user, Decision.APPROVE)

    def test_a_single_approver_completes_the_request(self):
        self.policy(PUBLISH, [self.checker1])
        req = self.approve(self.raise_request(), self.checker1)
        self.assertEqual(req.status, RequestStatus.EXECUTED)

    def test_any_one_approver_completes_the_request(self):
        self.policy(PUBLISH, [self.checker1, self.checker2], workflow=WorkflowType.MULTI)
        req = self.approve(self.raise_request(), self.checker1)
        self.assertEqual(req.status, RequestStatus.EXECUTED)

    def test_all_approvers_complete_the_request_with_the_last_approval(self):
        self.policy(PUBLISH, [self.checker1, self.checker2], workflow=WorkflowType.MULTI, require_all=True)
        req = self.raise_request()
        self.assertEqual(self.approve(req, self.checker1).status, RequestStatus.PENDING)
        self.assertEqual(self.approve(req, self.checker2).status, RequestStatus.EXECUTED)

    def test_an_approval_flow_moves_to_the_next_step_and_tells_its_approver(self):
        self.policy(PUBLISH, [self.checker1, self.checker2], workflow=WorkflowType.SEQUENTIAL)
        req = self.raise_request()
        with self.captureOnCommitCallbacks(execute=True):
            req = self.approve(req, self.checker1)
        self.assertEqual((req.status, req.current_stage), (RequestStatus.PENDING, 1))
        self.assertEqual(TestAdapter.outbox[-1].recipients, (self.checker2,))
        self.assertEqual(self.approve(req, self.checker2).status, RequestStatus.EXECUTED)

    def test_a_resubmitted_request_starts_again_from_the_first_step(self):
        self.policy(PUBLISH, [self.checker1, self.checker2], workflow=WorkflowType.SEQUENTIAL)
        req = self.raise_request()
        self.approve(req, self.checker1)
        services.decide(req.pk, self.checker2, Decision.REQUEST_CHANGES, "Fix the title")
        req = services.resubmit(req.pk, self.maker)
        self.assertEqual((req.status, req.current_stage), (RequestStatus.PENDING, 0))
        self.approve(req, self.checker1)
        self.assertEqual(self.approve(req, self.checker2).status, RequestStatus.EXECUTED)

    def test_the_same_person_still_cannot_decide_twice(self):
        self.policy(PUBLISH, [self.role_reviewer, self.role_reviewer], workflow=WorkflowType.MULTI, require_all=True)
        req = self.raise_request()
        self.approve(req, self.checker1)
        with self.assertRaisesMessage(services.ApprovalError, "You have already acted on this request."):
            self.approve(req, self.checker1)

    def test_an_overdue_step_escalates_and_the_backup_can_complete_it(self):
        self.policy(
            PUBLISH, [self.checker1], sla_enabled=True, sla_value=4, sla_unit=SlaUnit.HOURS, escalation_user=self.admin
        )
        req = self.raise_request()
        started = req.stages.using("default").get().started_at
        self.assertEqual(services.process_sla(started + timedelta(hours=5))["escalated"], 1)
        self.assertEqual(self.approve(req, self.admin).status, RequestStatus.EXECUTED)

    def test_cancelling_tells_the_approvers_of_the_current_step(self):
        self.policy(PUBLISH, [self.checker1])
        req = self.raise_request()
        with self.captureOnCommitCallbacks(execute=True):
            services.cancel(req.pk, self.maker)
        self.assertEqual(TestAdapter.outbox[-1].recipients, (self.checker1,))
