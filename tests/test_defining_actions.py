"""Defining actions: how a host puts an operation behind maker-checker.

An action is described by an ``ActionHandler`` held in a registry keyed by ``action_type``. There are three ways to
write one, all exercised by ``tests/testapp/handlers.py``:

* ``@approval_action`` on a service function (``publish_document``): the call is captured as a JSON payload and
  replayed on approval.
* ``ModelChangeHandler`` (``DocumentEditHandler``): "change these fields on one record", with snapshot, optimistic
  concurrency check and Current/Proposed review rows built in.
* A plain ``ActionHandler`` subclass (``FlakyHandler``) for anything else.

Whether a call is held for approval is data (a tenant's rules), never code: the call site is the same either way.
"""

from decimal import Decimal

from django_maker_checker import services
from django_maker_checker.actions import approval_action
from django_maker_checker.constants import Decision, RequestStatus
from django_maker_checker.exceptions import ApprovalError
from django_maker_checker.model_change import ModelChangeHandler
from django_maker_checker.registry import Scope, all_handlers, diff_rows, get_handler, register_handler
from tests.base import EngineTestCase
from tests.testapp.handlers import EDIT, FLAKY, PUBLISH, DocumentEditHandler, publish_document
from tests.testapp.models import Document


class TheRegistry(EngineTestCase):
    def test_each_action_derives_the_permission_its_approvers_need(self):
        handler = get_handler(PUBLISH)
        self.assertEqual(handler.approve_permission, "approve_document_publish")
        self.assertEqual(handler.approve_permission_label, "Approve Publish Document")

    def test_unknown_action_types_are_a_lookup_error(self):
        with self.assertRaisesMessage(LookupError, "No maker-checker handler registered for 'nope'"):
            get_handler("nope")

    def test_handlers_are_listed_by_category_and_label_and_can_be_narrowed_by_scope(self):
        self.assertEqual([h.action_type for h in all_handlers()], [EDIT, PUBLISH, FLAKY])
        self.assertEqual([h.action_type for h in all_handlers(Scope.SCOPED)], [PUBLISH])

    def test_one_action_type_cannot_be_defined_in_two_places(self):
        with self.assertRaisesMessage(ValueError, "already registered"):
            approval_action(PUBLISH, label="x", category="x", lock_key=str)(lambda **kw: None)

        class Impostor(ModelChangeHandler):
            action_type = EDIT
            model = Document

        with self.assertRaisesMessage(ValueError, "already registered"):
            register_handler(Impostor())

    def test_re_registering_the_same_definition_is_allowed_for_module_reloads(self):
        self.assertIs(register_handler(get_handler(EDIT)), get_handler(EDIT))

    def test_the_generic_review_panel_compares_snapshot_and_payload_row_by_row(self):
        rows = diff_rows({"amount": "10.00", "title": "Q3"}, {"amount": "12.00", "title": "Q3"}, {"amount": "Total"})
        self.assertEqual(
            rows,
            [
                {"label": "Total", "current": "10.00", "proposed": "12.00", "changed": True},
                {"label": "Title", "current": "Q3", "proposed": "Q3", "changed": False},
            ],
        )


class ApprovalActionDecorator(EngineTestCase):
    def test_the_decorated_function_is_registered_as_a_handler(self):
        self.assertIs(publish_document.handler, get_handler(PUBLISH))

    def test_without_a_rule_the_function_runs_now_and_returns_its_result(self):
        outcome = self.publish(note="now")
        self.assertFalse(outcome.pending)
        self.assertEqual(outcome.result, {"document_id": self.document.pk, "by": "maker", "note": "now"})

    def test_with_a_rule_the_call_is_captured_and_replayed_on_approval_as_the_makers_change(self):
        self.policy(PUBLISH, [self.role_reviewer])
        req = self.raise_request(note="later")
        self.assertEqual(req.payload, {"document_id": self.document.pk, "note": "later"})
        self.assertEqual(req.lock_key, f"document:{self.document.pk}")
        self.assertEqual(req.target_label, "Q3 report")

        req = services.decide(req.pk, self.checker1, Decision.APPROVE)
        self.assertEqual(req.result["by"], "maker")

    def test_payloads_go_through_json_on_both_paths_so_behaviour_never_depends_on_the_rule(self):
        direct = self.publish(note=Decimal("1.50"))
        self.assertEqual(direct.result["note"], "1.50")

        self.document.published = False
        self.document.save()
        self.policy(PUBLISH, [self.role_reviewer])
        req = services.decide(self.raise_request(note=Decimal("1.50")).pk, self.checker1, Decision.APPROVE)
        self.assertEqual(req.result["note"], "1.50")

    def test_payloads_that_are_not_json_serialisable_are_refused_up_front(self):
        with self.assertRaisesMessage(TypeError, "JSON-serialisable"):
            self.publish(note=object())

    def test_apply_bypasses_approval_for_tests_and_admin_tools(self):
        self.policy(PUBLISH, [self.role_reviewer])
        result = publish_document.apply(
            tenant=self.tenant, maker=self.admin, context=None, document_id=self.document.pk
        )
        self.assertEqual(result["by"], "admin")


class ModelChangeHandlers(EngineTestCase):
    def submit(self, changes, **kwargs):
        return DocumentEditHandler.submit_change(self.document, changes, maker=self.maker, **kwargs)

    def test_without_a_rule_the_change_is_applied_now(self):
        self.assertFalse(self.submit({"amount": "2.50"}).pending)
        self.document.refresh_from_db()
        self.assertEqual(self.document.amount, Decimal("2.50"))

    def test_the_request_snapshots_current_values_and_locks_the_record(self):
        self.policy(EDIT, [self.role_reviewer])
        req = self.submit({"amount": Decimal("5.00"), "title": "Q3 final"}).approval_request
        self.assertEqual(req.before_snapshot, {"amount": "10.00", "title": "Q3 report"})
        self.assertEqual(req.lock_key, f"testapp.document:{self.document.pk}")

    def test_reviewers_see_current_and_proposed_values_under_the_fields_verbose_names(self):
        self.policy(EDIT, [self.role_reviewer])
        req = self.submit({"amount": Decimal("5.00")}).approval_request
        (row,) = get_handler(EDIT).review_context(req)["rows"]
        self.assertEqual((row["label"], row["current"], row["proposed"]), ("Amount", "10.00", "5.00"))

    def test_approval_converts_values_back_to_field_types_and_saves_them(self):
        self.policy(EDIT, [self.role_reviewer])
        req = self.submit({"amount": Decimal("5.00"), "title": "Q3 final"}).approval_request
        self.assertEqual(services.decide(req.pk, self.checker1, Decision.APPROVE).status, RequestStatus.EXECUTED)
        self.document.refresh_from_db()
        self.assertEqual((self.document.amount, self.document.title), (Decimal("5.00"), "Q3 final"))

    def test_an_edit_made_after_submission_fails_the_request_instead_of_being_overwritten(self):
        self.policy(EDIT, [self.role_reviewer])
        req = self.submit({"amount": "5.00"}).approval_request
        Document.objects.filter(pk=self.document.pk).update(amount=Decimal("9.99"))

        req = services.decide(req.pk, self.checker1, Decision.APPROVE)
        self.assertEqual(req.status, RequestStatus.FAILED)
        self.assertIn("changed after this request was raised", req.execution_error)

    def test_model_validation_errors_become_the_failure_reason(self):
        self.policy(EDIT, [self.role_reviewer])
        req = self.submit({"title": ""}).approval_request
        req = services.decide(req.pk, self.checker1, Decision.APPROVE)
        self.assertEqual(req.status, RequestStatus.FAILED)
        self.assertIn("cannot be blank", req.execution_error)

    def test_only_the_declared_fields_can_change(self):
        with self.assertRaisesMessage(ValueError, "cannot change: published"):
            self.submit({"published": True})

    def test_records_are_found_through_the_tenant_so_another_tenants_record_is_out_of_reach(self):
        theirs = Document.objects.create(tenant=self.other_tenant, title="Theirs")
        direct = type(
            "Direct",
            (),
            {"tenant": self.tenant, "before_snapshot": {}, "payload": {"pk": theirs.pk, "changes": {"title": "x"}}},
        )()
        with self.assertRaisesMessage(ApprovalError, "no longer exists"):
            get_handler(EDIT).execute(direct, None)
