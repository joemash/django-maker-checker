"""Notifications: who is told what, and when.

The engine decides *who* hears about an event and builds a ``Notice`` (kind, recipients, text, link, severity); the
host's adapter delivers it (in-app, email, chat). Notices are sent only after the transaction commits, and a failing
delivery never undoes the change that triggered it.

    requested    current approvers (and their delegates): a request is waiting for them
    decided      the maker: approved, executed or rejected (with the reason)
    failed       the maker: approved but could not be applied
    cancelled    the current approvers, and the maker when an administrator cancelled
    sla_warning  current approvers: the deadline is near
    escalated    the backup approver, or the current approvers when there is none
"""

from datetime import timedelta

from django.utils import timezone

from django_maker_checker import services
from django_maker_checker.constants import Decision, SlaUnit
from tests.base import EngineTestCase
from tests.testapp.adapter import TestAdapter
from tests.testapp.handlers import FLAKY, PUBLISH


class WhoIsTold(EngineTestCase):
    def submit(self, **policy_kwargs):
        self.policy(PUBLISH, [self.role_reviewer], **policy_kwargs)
        with self.captureOnCommitCallbacks(execute=True):
            return self.raise_request()

    def last(self):
        return TestAdapter.outbox[-1]

    def test_a_rejection_tells_the_maker_why_with_a_link_to_the_request(self):
        req = self.submit()
        with self.captureOnCommitCallbacks(execute=True):
            services.decide(req.pk, self.checker1, Decision.REJECT, "Missing appendix")
        self.assertEqual((self.last().kind, self.last().recipients), ("decided", (self.maker,)))
        self.assertIn("Missing appendix", self.last().text)
        self.assertEqual(self.last().link, f"/approvals/?open={req.reference}")

    def test_an_administrators_cancellation_tells_approvers_and_the_maker(self):
        req = self.submit()
        with self.captureOnCommitCallbacks(execute=True):
            services.cancel(req.pk, self.admin, "Duplicate")
        self.assertEqual(self.last().kind, "cancelled")
        self.assertEqual({u.username for u in self.last().recipients}, {"maker", "checker1", "checker2"})

    def test_a_failed_execution_tells_the_maker_as_an_error(self):
        self.policy(FLAKY, [self.role_reviewer])
        req = services.submit(FLAKY, tenant=self.tenant, maker=self.maker, payload={"n": 7})
        with self.captureOnCommitCallbacks(execute=True):
            services.decide(req.pk, self.checker1, Decision.APPROVE)
        self.assertEqual((self.last().recipients, self.last().severity), ((self.maker,), "error"))

    def test_without_a_backup_approver_escalation_reminds_the_current_approvers(self):
        req = self.submit(sla_enabled=True, sla_value=1, sla_unit=SlaUnit.HOURS)
        start = req.active_stage.started_at
        with self.captureOnCommitCallbacks(execute=True):
            services.process_sla(start + timedelta(minutes=50))
            services.process_sla(start + timedelta(hours=2))
        self.assertEqual([n.kind for n in TestAdapter.outbox[-2:]], ["sla_warning", "escalated"])
        self.assertIn("no escalation destination", req.events.filter(type="escalated").get().message)

    def test_delegates_are_told_alongside_the_approver_they_stand_in_for(self):
        now = timezone.now()
        services.delegate(
            delegator=self.checker1,
            delegate_user=self.admin,
            starts_at=now - timedelta(hours=1),
            ends_at=now + timedelta(days=1),
        )
        self.policy(PUBLISH, [self.checker1])
        with self.captureOnCommitCallbacks(execute=True):
            self.raise_request()
        self.assertEqual(set(self.last().recipients), {self.checker1, self.admin})


class DeliveryFailures(EngineTestCase):
    def test_a_failing_notifier_does_not_undo_the_request(self):
        def broken(adapter, notice):
            raise RuntimeError("smtp down")

        original = TestAdapter.notify
        TestAdapter.notify = broken
        self.addCleanup(setattr, TestAdapter, "notify", original)

        self.policy(PUBLISH, [self.role_reviewer])
        with self.captureOnCommitCallbacks(execute=True):
            req = self.raise_request()
        req.refresh_from_db()
        self.assertEqual(req.status, "pending")
