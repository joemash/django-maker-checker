"""Cancelling, commenting and viewing: everything else that can happen to a pending request.

Every step in a request's life is an ``ApprovalEvent`` on its timeline (submitted, viewed, commented, approved,
rejected, cancelled, escalated, executed), which is the audit trail reviewers and auditors read.
"""

from django_maker_checker import services
from django_maker_checker.constants import Decision, EventType, RequestStatus
from django_maker_checker.exceptions import ApprovalError
from django_maker_checker.permissions import can_view
from tests.base import EngineTestCase
from tests.testapp.handlers import PUBLISH


class Cancelling(EngineTestCase):
    def setUp(self):
        super().setUp()
        self.policy(PUBLISH, [self.role_reviewer])
        self.req = self.raise_request()

    def test_the_maker_may_withdraw_their_request_with_a_reason(self):
        req = services.cancel(self.req.pk, self.maker, "No longer needed")
        self.assertEqual(req.status, RequestStatus.CANCELLED)
        self.assertEqual(req.events.last().message, "Cancelled by the maker. Reason: No longer needed")

    def test_an_administrator_may_cancel_anyones_request_and_is_named(self):
        req = services.cancel(self.req.pk, self.admin)
        self.assertEqual(req.decided_by, self.admin)
        self.assertEqual(req.events.last().message, "Cancelled by Admin Test.")

    def test_approvers_cannot_cancel(self):
        with self.assertRaisesMessage(ApprovalError, "cannot cancel"):
            services.cancel(self.req.pk, self.checker1)

    def test_a_decided_request_cannot_be_cancelled(self):
        services.decide(self.req.pk, self.checker1, Decision.APPROVE)
        with self.assertRaisesMessage(ApprovalError, "cannot cancel"):
            services.cancel(self.req.pk, self.maker)

    def test_cancelling_frees_the_item_for_a_new_request(self):
        services.cancel(self.req.pk, self.maker)
        self.assertTrue(self.publish().pending)


class Commenting(EngineTestCase):
    def setUp(self):
        super().setUp()
        self.policy(PUBLISH, [self.role_reviewer])
        self.req = self.raise_request()

    def test_anyone_who_may_view_the_request_may_comment(self):
        services.add_comment(self.req.pk, self.checker1, "Checking the figures")
        services.add_comment(self.req.pk, self.maker, "Figures are in the appendix")
        comments = self.req.events.filter(type=EventType.COMMENT).values_list("message", flat=True)
        self.assertEqual(list(comments), ["Checking the figures", "Figures are in the appendix"])

    def test_empty_comments_and_outsiders_are_refused(self):
        with self.assertRaisesMessage(ApprovalError, "cannot be empty"):
            services.add_comment(self.req.pk, self.checker1, "   ")
        with self.assertRaises(ApprovalError):
            services.add_comment(self.req.pk, self.outsider, "hi")


class Viewing(EngineTestCase):
    def setUp(self):
        super().setUp()
        self.policy(PUBLISH, [self.checker1])
        self.req = self.raise_request()

    def test_who_may_open_a_request(self):
        self.assertTrue(can_view(self.maker, self.req))
        self.assertTrue(can_view(self.admin, self.req))
        self.assertTrue(can_view(self.checker2, self.req))  # not in this rule, but approves this kind of action
        self.assertFalse(can_view(self.outsider, self.req))

    def test_only_the_first_view_by_a_current_approver_is_logged(self):
        services.record_view(self.req, self.checker1)
        services.record_view(self.req, self.checker1)
        services.record_view(self.req, self.maker)
        services.record_view(self.req, self.checker2)
        viewers = self.req.events.filter(type=EventType.VIEWED).values_list("actor__username", flat=True)
        self.assertEqual(list(viewers), ["checker1"])
