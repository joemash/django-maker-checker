"""SLA and escalation: keeping requests from waiting forever.

A rule with an SLA gives every stage its own time limit, counted from when that stage started. The periodic task
``approvals.tasks.process_approval_slas`` (a Celery beat job in the host) runs ``services.process_sla``, which:

* reminds the current approvers once 75% of the window has passed (``sla_warned_at``), and
* escalates once the deadline passes (``escalated_at``): the rule's backup approver gets a slot in the stage and
  may then approve on their own. Without a backup, the current approvers are reminded instead.

Each happens at most once per stage, and concurrent runs split the work rather than double-processing it.
"""

from datetime import timedelta

from django_maker_checker import services
from django_maker_checker.constants import Decision, EventType, RequestStatus, SlaUnit
from django_maker_checker.exceptions import ApprovalError
from django_maker_checker.tasks import process_approval_slas
from tests.base import EngineTestCase
from tests.testapp.handlers import PUBLISH


class SlaPass(EngineTestCase):
    def setUp(self):
        super().setUp()
        self.policy(
            PUBLISH, [self.checker1], sla_enabled=True, sla_value=4, sla_unit=SlaUnit.HOURS, escalation_user=self.admin
        )
        self.req = self.raise_request()
        self.start = self.req.active_stage.started_at

    def run_at(self, **after_start):
        return services.process_sla(self.start + timedelta(**after_start))

    def test_nothing_happens_early_in_the_window(self):
        self.assertEqual(self.run_at(hours=1), {"warned": 0, "escalated": 0})

    def test_approvers_are_warned_once_when_the_deadline_nears(self):
        self.assertEqual(self.run_at(hours=3, minutes=10)["warned"], 1)
        self.assertEqual(self.run_at(hours=3, minutes=20)["warned"], 0)
        self.assertEqual(self.req.events.filter(type=EventType.SLA_WARNING).count(), 1)

    def test_an_overdue_stage_escalates_once(self):
        self.assertEqual(self.run_at(hours=5)["escalated"], 1)
        self.assertEqual(self.run_at(hours=6)["escalated"], 0)
        self.assertEqual(self.req.events.filter(type=EventType.ESCALATED).count(), 1)

    def test_the_backup_approver_may_decide_only_after_escalation(self):
        with self.assertRaises(ApprovalError):
            services.decide(self.req.pk, self.admin, Decision.APPROVE)
        self.run_at(hours=5)
        self.assertEqual(services.decide(self.req.pk, self.admin, Decision.APPROVE).status, RequestStatus.EXECUTED)

    def test_decided_requests_are_left_alone(self):
        services.decide(self.req.pk, self.checker1, Decision.APPROVE)
        self.assertEqual(self.run_at(hours=5), {"warned": 0, "escalated": 0})

    def test_the_periodic_task_runs_the_sla_pass(self):
        self.assertEqual(process_approval_slas(), {"warned": 0, "escalated": 0})
