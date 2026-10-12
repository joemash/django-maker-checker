"""Queues and display: what host pages read from ``queries``.

The engine ships no views or templates. ``queries`` gives a host everything an approvals page needs, already
filtered by what the viewer may see:

    awaiting(user)              requests waiting for the user's decision now
    made_by(user, open_only)    the user's own requests
    checker_history(user)       requests the user may review that are no longer open
    pending_count(user)         badge count, cached per tenant
    allowed_actions(user, req)  which buttons to show
    sla_status(req)             the current stage's SLA, for a progress bar
    stage_progress(req)         every stage, for a step strip
"""

from datetime import timedelta

from django_maker_checker import queries, services
from django_maker_checker.constants import Decision, SlaState, WorkflowType
from tests.base import EngineTestCase
from tests.testapp.handlers import PUBLISH


class RequestQueues(EngineTestCase):
    def setUp(self):
        super().setUp()
        self.policy(PUBLISH, [self.role_reviewer])
        self.req = self.raise_request()

    def test_approvers_see_what_awaits_them_and_makers_see_their_own(self):
        self.assertEqual(queries.awaiting(self.checker1), [self.req])
        self.assertEqual(queries.awaiting(self.maker), [])
        self.assertEqual(queries.made_by(self.maker, open_only=True), [self.req])
        self.assertEqual(queries.pending_count(self.checker1), 1)

    def test_decided_requests_leave_the_queues_and_move_to_history(self):
        services.decide(self.req.pk, self.checker1, Decision.APPROVE)
        self.assertEqual(queries.pending_count(self.checker2), 0)
        self.assertEqual(queries.made_by(self.maker, open_only=True), [])
        self.assertEqual(queries.made_by(self.maker, open_only=False), [self.req])
        self.assertEqual(queries.checker_history(self.checker2), [self.req])

    def test_other_tenants_see_nothing(self):
        self.assertEqual(queries.awaiting(self.outsider), [])
        self.assertEqual(queries.checker_history(self.outsider), [])


class Filtering(EngineTestCase):
    def setUp(self):
        super().setUp()
        self.policy(PUBLISH, [self.role_reviewer], risk_level="high")
        self.req = self.raise_request()

    def awaiting(self, **filters):
        return queries.awaiting(self.checker1, queries.RequestFilter(**filters))

    def test_exact_filters_must_all_match(self):
        self.assertEqual(self.awaiting(risk_level="high", maker_id=self.maker.pk), [self.req])
        self.assertEqual(self.awaiting(risk_level="low"), [])

    def test_text_search_covers_reference_target_action_and_maker_case_insensitively(self):
        for text in [self.req.reference.lower(), "Q3 REPORT", "publish", "maker@example", "Maker Test"]:
            with self.subTest(text):
                self.assertEqual(self.awaiting(text=text), [self.req])
        self.assertEqual(self.awaiting(text="nothing like it"), [])

    def test_date_ranges_use_the_submission_day(self):
        day = self.req.submitted_at.date()
        self.assertEqual(self.awaiting(submitted_from=day, submitted_to=day), [self.req])
        self.assertEqual(self.awaiting(submitted_from=day + timedelta(days=1)), [])

    def test_the_maker_filter_offers_each_maker_once(self):
        self.assertEqual(queries.makers([self.req, self.req]), [self.maker])


class AllowedActions(EngineTestCase):
    def test_each_viewer_gets_only_the_buttons_they_may_use(self):
        self.policy(PUBLISH, [self.role_reviewer])
        req = self.raise_request()
        self.assertEqual(queries.allowed_actions(self.checker1, req), queries.AllowedActions(decide=True))
        self.assertEqual(queries.allowed_actions(self.maker, req), queries.AllowedActions(cancel=True, is_maker=True))
        self.assertEqual(queries.allowed_actions(self.admin, req), queries.AllowedActions(cancel=True))


class SlaStatus(EngineTestCase):
    def setUp(self):
        super().setUp()
        self.policy(
            PUBLISH, [self.role_reviewer], sla_enabled=True, sla_value=4, sla_unit="hours", escalation_user=self.admin
        )
        self.req = self.raise_request()
        self.start = self.req.active_stage.started_at

    def status_after(self, **elapsed):
        return queries.sla_status(self.req, now=self.start + timedelta(**elapsed))

    def test_on_track_below_sixty_percent_of_the_window(self):
        status = self.status_after(hours=1)
        self.assertEqual((status.state, status.percent, status.limit), (SlaState.ON_TRACK, 25, "4 hours"))
        self.assertEqual(status.remaining, "3h 00m")

    def test_at_risk_from_sixty_percent(self):
        self.assertEqual(self.status_after(hours=2, minutes=30).state, SlaState.AT_RISK)

    def test_past_the_deadline_it_shows_overdue_until_escalated_then_names_the_backup(self):
        overdue = self.status_after(hours=5)
        self.assertEqual((overdue.state, overdue.label), (SlaState.ESCALATED, "Overdue"))
        self.assertEqual(overdue.remaining, "overdue by 1h 00m")
        services.process_sla(self.start + timedelta(hours=5))
        self.req.refresh_from_db()
        escalated = self.status_after(hours=5)
        self.assertEqual((escalated.label, escalated.escalated_to), ("Escalated", "Admin Test"))

    def test_requests_that_are_no_longer_pending_have_no_sla(self):
        services.decide(self.req.pk, self.checker1, Decision.APPROVE)
        self.req.refresh_from_db()
        self.assertEqual(queries.sla_status(self.req).state, SlaState.NONE)


class StageProgress(EngineTestCase):
    def test_each_stage_reports_its_state_and_approvals_done_of_required(self):
        self.policy(PUBLISH, [self.checker1, self.admin], workflow=WorkflowType.SEQUENTIAL)
        req = self.raise_request()
        self.assertEqual(queries.current_approvers_label(req), "Checker1 Test")
        req = services.decide(req.pk, self.checker1, Decision.APPROVE)
        first, second = queries.stage_progress(req)
        self.assertEqual((first.number, first.state, first.done, first.required), (1, "done", 1, 1))
        self.assertEqual((second.number, second.state, second.done, second.required), (2, "current", 0, 1))

    def test_require_all_stages_need_every_regular_approver(self):
        self.policy(PUBLISH, [self.checker1, self.checker2], workflow=WorkflowType.MULTI, require_all=True)
        req = self.raise_request()
        services.decide(req.pk, self.checker1, Decision.APPROVE)
        (stage,) = queries.stage_progress(req)
        self.assertEqual((stage.state, stage.done, stage.required), ("current", 1, 2))
