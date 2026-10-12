"""Delegating: an approver hands their approvals to a colleague for a period (e.g. while on leave).

A delegation names a delegator, a delegate, a window (end exclusive) and optionally the action types it covers
(empty means all). While it is current, the delegate may decide wherever the delegator could; the decision records
both people. Segregation of duties still holds: a maker never approves their own request through a delegation.
"""

from datetime import timedelta

from django.utils import timezone

from django_maker_checker import services
from django_maker_checker.constants import Decision, RequestStatus
from django_maker_checker.exceptions import ApprovalError
from django_maker_checker.models import ApprovalDelegation
from tests.base import EngineTestCase
from tests.testapp.handlers import EDIT, PUBLISH


class DelegatedApproval(EngineTestCase):
    def setUp(self):
        super().setUp()
        self.policy(PUBLISH, [self.checker1])
        self.req = self.raise_request()
        self.now = timezone.now()

    def delegate_to(self, delegate, **kwargs):
        kwargs.setdefault("starts_at", self.now - timedelta(hours=1))
        kwargs.setdefault("ends_at", self.now + timedelta(days=1))
        return services.delegate(delegator=self.checker1, delegate_user=delegate, **kwargs)

    def test_the_delegate_decides_on_behalf_of_the_approver(self):
        self.delegate_to(self.admin)
        req = services.decide(self.req.pk, self.admin, Decision.APPROVE)
        self.assertEqual(req.status, RequestStatus.EXECUTED)
        decision = req.decisions.get()
        self.assertEqual((decision.actor, decision.on_behalf_of), (self.admin, self.checker1))

    def test_a_delegation_limited_to_other_actions_grants_nothing_here(self):
        self.delegate_to(self.admin, action_types=[EDIT])
        with self.assertRaises(ApprovalError):
            services.decide(self.req.pk, self.admin, Decision.APPROVE)

    def test_a_revoked_delegation_grants_nothing(self):
        services.revoke_delegation(self.delegate_to(self.admin), self.checker1)
        with self.assertRaises(ApprovalError):
            services.decide(self.req.pk, self.admin, Decision.APPROVE)

    def test_a_delegation_that_has_not_started_grants_nothing(self):
        self.delegate_to(self.admin, starts_at=self.now + timedelta(hours=1))
        with self.assertRaises(ApprovalError):
            services.decide(self.req.pk, self.admin, Decision.APPROVE)

    def test_the_maker_cannot_approve_their_own_request_through_a_delegation(self):
        ApprovalDelegation.objects.create(
            tenant=self.tenant,
            delegator=self.checker1,
            delegate=self.maker,
            starts_at=self.now - timedelta(hours=1),
            ends_at=self.now + timedelta(days=1),
        )
        with self.assertRaises(ApprovalError):
            services.decide(self.req.pk, self.maker, Decision.APPROVE)


class DelegationRules(EngineTestCase):
    def setUp(self):
        super().setUp()
        self.now = timezone.now()

    def delegate(self, delegator, delegate, ends_in=timedelta(days=1)):
        return services.delegate(
            delegator=delegator, delegate_user=delegate, starts_at=self.now, ends_at=self.now + ends_in
        )

    def test_only_approvers_can_delegate(self):
        with self.assertRaisesMessage(ApprovalError, "Only approvers"):
            self.delegate(self.maker, self.admin)

    def test_the_delegate_must_be_an_active_member_of_the_same_tenant(self):
        with self.assertRaisesMessage(ApprovalError, "active member"):
            self.delegate(self.checker1, self.outsider)

    def test_nobody_delegates_to_themselves(self):
        with self.assertRaisesMessage(ApprovalError, "delegate to yourself"):
            self.delegate(self.checker1, self.checker1)

    def test_the_window_must_end_after_it_starts(self):
        with self.assertRaisesMessage(ApprovalError, "after its start"):
            self.delegate(self.checker1, self.admin, ends_in=-timedelta(days=1))

    def test_only_the_delegator_or_its_creator_may_revoke(self):
        delegation = self.delegate(self.checker1, self.admin)
        with self.assertRaisesMessage(ApprovalError, "cannot end this delegation"):
            services.revoke_delegation(delegation, self.admin)
        self.assertIsNotNone(services.revoke_delegation(delegation, self.checker1).revoked_at)
