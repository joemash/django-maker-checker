"""Switching approvals off: a host can turn maker-checker off for a tenant without losing its setup.

``ApprovalsAdapter.is_enabled(tenant)`` (default ``True``) is how a host says whether maker-checker is on for a
tenant, typically from a feature flag or a plan. While it is off the tenant is treated as having no rules: every
guarded action runs immediately, so nothing waits for an approval screen the host may be hiding. Rules, history and
requests are kept, and switching it back on resumes as before.
"""

from datetime import timedelta

from django_maker_checker import services
from django_maker_checker.constants import Decision, RequestStatus, SlaUnit
from django_maker_checker.exceptions import ApprovalError
from tests.base import EngineTestCase
from tests.testapp.adapter import TestAdapter
from tests.testapp.handlers import EDIT, PUBLISH, DocumentEditHandler


class WhileSwitchedOff(EngineTestCase):
    def setUp(self):
        super().setUp()
        self.rule = self.policy(PUBLISH, [self.role_reviewer])
        TestAdapter.disabled_tenant_ids.add(self.tenant.pk)

    def test_no_rule_applies_so_guarded_actions_run_immediately(self):
        self.assertFalse(services.requires_approval(self.tenant, PUBLISH, {"document_id": self.document.pk}))
        outcome = self.publish()
        self.assertFalse(outcome.pending)
        self.document.refresh_from_db()
        self.assertTrue(self.document.published)

    def test_model_change_handlers_apply_immediately_too(self):
        self.policy(EDIT, [self.role_reviewer])
        self.assertFalse(
            DocumentEditHandler.submit_change(self.document, {"title": "Q3 final"}, maker=self.maker).pending
        )

    def test_a_request_cannot_be_raised_even_with_an_explicit_rule(self):
        with self.assertRaisesMessage(ApprovalError, "switched off"):
            services.submit(
                PUBLISH,
                tenant=self.tenant,
                maker=self.maker,
                payload={"document_id": self.document.pk},
                policy=self.rule,
            )

    def test_other_tenants_are_unaffected(self):
        TestAdapter.disabled_tenant_ids.clear()
        TestAdapter.disabled_tenant_ids.add(self.other_tenant.pk)
        self.assertTrue(self.publish().pending)


class RequestsRaisedBeforeSwitchingOff(EngineTestCase):
    def setUp(self):
        super().setUp()
        self.policy(
            PUBLISH, [self.checker1], sla_enabled=True, sla_value=4, sla_unit=SlaUnit.HOURS, escalation_user=self.admin
        )
        self.req = self.raise_request()
        TestAdapter.disabled_tenant_ids.add(self.tenant.pk)

    def test_the_sla_job_leaves_them_alone(self):
        later = self.req.active_stage.started_at + timedelta(hours=5)
        self.assertEqual(services.process_sla(later), {"warned": 0, "escalated": 0})

    def test_they_stay_open_and_can_still_be_cancelled(self):
        self.assertEqual(services.cancel(self.req.pk, self.maker).status, RequestStatus.CANCELLED)

    def test_switching_back_on_resumes_where_it_stopped(self):
        TestAdapter.disabled_tenant_ids.clear()
        later = self.req.active_stage.started_at + timedelta(hours=5)
        self.assertEqual(services.process_sla(later)["escalated"], 1)
        self.assertEqual(services.decide(self.req.pk, self.admin, Decision.APPROVE).status, RequestStatus.EXECUTED)
