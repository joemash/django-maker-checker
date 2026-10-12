"""Locking items: a request that changes several items holds all of them until it closes.

Every request already holds its ``lock_key``, which stops a second open request of the same action for the same
item. A handler can also return ``lock_keys``: every item the change touches, e.g. each transaction in a manual
match. Those items are locked across all action types, so no other open request can claim any of them, and
``queries.open_requests_for`` finds the request holding an item, e.g. to badge it "Pending approval".

Locks are taken with the request and released when it closes (executed, rejected, cancelled or failed). Retrying a
failed request takes them again, and resubmitting with an amended payload swaps them for the new items.
"""

from django_maker_checker import queries, services
from django_maker_checker.constants import Decision, RequestStatus
from django_maker_checker.exceptions import ApprovalError
from tests.base import EngineTestCase
from tests.testapp.handlers import FLAKY, MERGE, PUBLISH, FlakyHandler, merge_documents
from tests.testapp.models import Document


class LockingSeveralItems(EngineTestCase):
    def setUp(self):
        super().setUp()
        self.second = Document.objects.create(tenant=self.tenant, title="Q3 appendix")
        self.third = Document.objects.create(tenant=self.tenant, title="Q3 notes")
        self.policy(MERGE, [self.role_reviewer])
        self.policy(PUBLISH, [self.role_reviewer])

    def merge(self, *documents, maker=None):
        ids = [d.pk for d in documents]
        return merge_documents(tenant=self.tenant, maker=maker or self.maker, document_ids=ids, title="Q3 pack")

    def test_a_request_holds_every_item_it_changes(self):
        req = self.merge(self.document, self.second).approval_request
        held = queries.open_requests_for(self.tenant, [f"document:{self.document.pk}", f"document:{self.second.pk}"])
        self.assertEqual(set(held.values()), {req})
        self.assertEqual(queries.open_requests_for(self.tenant, [f"document:{self.third.pk}"]), {})

    def test_another_request_touching_any_of_those_items_is_refused_naming_the_holder(self):
        req = self.merge(self.document, self.second).approval_request
        with self.assertRaisesMessage(ApprovalError, f"Another open approval request ({req.reference})"):
            self.merge(self.second, self.third, maker=self.admin)

    def test_items_are_locked_across_actions(self):
        req = self.merge(self.document, self.second).approval_request
        with self.assertRaisesMessage(ApprovalError, req.reference):
            self.publish()

    def test_a_refused_submission_leaves_nothing_behind(self):
        self.merge(self.document, self.second)
        with self.assertRaises(ApprovalError):
            self.merge(self.second, self.third, maker=self.admin)
        self.assertEqual(queries.open_requests_for(self.tenant, [f"document:{self.third.pk}"]), {})
        self.assertTrue(self.merge(self.third, maker=self.admin).pending)


class ReleasingLocks(EngineTestCase):
    def setUp(self):
        super().setUp()
        self.second = Document.objects.create(tenant=self.tenant, title="Q3 appendix")
        self.policy(MERGE, [self.role_reviewer])
        self.req = merge_documents(
            tenant=self.tenant, maker=self.maker, document_ids=[self.document.pk, self.second.pk], title="Q3 pack"
        ).approval_request
        self.keys = [f"document:{self.document.pk}", f"document:{self.second.pk}"]

    def assert_released(self):
        self.assertEqual(queries.open_requests_for(self.tenant, self.keys), {})

    def test_when_the_change_is_applied(self):
        self.assertEqual(services.decide(self.req.pk, self.checker1, Decision.APPROVE).status, RequestStatus.EXECUTED)
        self.assert_released()

    def test_when_rejected(self):
        services.decide(self.req.pk, self.checker1, Decision.REJECT, "Not these two")
        self.assert_released()

    def test_when_cancelled(self):
        services.cancel(self.req.pk, self.maker)
        self.assert_released()

    def test_not_while_sent_back_for_changes(self):
        services.decide(self.req.pk, self.checker1, Decision.REQUEST_CHANGES, "Add the notes too")
        self.assertEqual(len(queries.open_requests_for(self.tenant, self.keys)), 2)

    def test_resubmitting_amended_items_swaps_the_locks(self):
        third = Document.objects.create(tenant=self.tenant, title="Q3 notes")
        services.decide(self.req.pk, self.checker1, Decision.REQUEST_CHANGES, "Merge the notes, not the appendix")
        services.resubmit(
            self.req.pk, self.maker, payload={"document_ids": [self.document.pk, third.pk], "title": "Q3 pack"}
        )
        held = queries.open_requests_for(self.tenant, [*self.keys, f"document:{third.pk}"])
        self.assertEqual(sorted(held), sorted([f"document:{self.document.pk}", f"document:{third.pk}"]))


class FailedRequests(EngineTestCase):
    def setUp(self):
        super().setUp()
        self.policy(FLAKY, [self.role_reviewer])
        req = services.submit(FLAKY, tenant=self.tenant, maker=self.maker, payload={"n": 1})
        self.req = services.decide(req.pk, self.checker1, Decision.APPROVE)

    def test_a_failed_request_releases_its_items(self):
        self.assertEqual(self.req.status, RequestStatus.FAILED)
        self.assertEqual(queries.open_requests_for(self.tenant, ["item:1"]), {})

    def test_retrying_takes_the_items_again(self):
        FlakyHandler.fail = False
        self.addCleanup(setattr, FlakyHandler, "fail", True)
        self.assertEqual(services.retry(self.req.pk, self.admin).status, RequestStatus.EXECUTED)

    def test_retrying_is_refused_if_another_request_has_since_claimed_the_items(self):
        other = services.submit(FLAKY, tenant=self.tenant, maker=self.admin, payload={"n": 1})
        with self.assertRaisesMessage(ApprovalError, other.reference):
            services.retry(self.req.pk, self.admin)
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, RequestStatus.FAILED)
