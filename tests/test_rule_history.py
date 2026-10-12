"""Rule history: every change to a rule is kept, numbered and audited.

``save_policy``, ``add_scoped_policy`` and ``delete_policy`` each write an append-only ``ApprovalPolicyRevision``
in the same transaction as the change, and send it to the host audit log. A revision stores the rule in display
form (names, not ids, so it still reads correctly after people or roles change), the field-by-field changes, who
made them and why. Versions are numbered per tenant, action and scope, so a rule that is deleted and created again
continues its numbering. Requests record the version they were raised under.
"""

from django_maker_checker import policies, queries
from django_maker_checker.constants import PolicyChange, WorkflowType
from django_maker_checker.policies import PolicySpec
from tests.base import EngineTestCase
from tests.testapp.adapter import TestAdapter
from tests.testapp.handlers import PUBLISH


class RuleHistory(EngineTestCase):
    def save(self, reason="", **spec):
        spec.setdefault("approvers", [f"r:{self.role_reviewer.pk}"])
        return policies.save_policy(
            policies.policy_for(self.tenant, PUBLISH), PolicySpec(**spec), by=self.admin, reason=reason
        )

    def history(self):
        return queries.policy_history(self.tenant, PUBLISH)

    def test_creating_a_rule_is_version_one_and_is_audited(self):
        self.save(reason="Board decision")
        (revision,) = self.history()
        self.assertEqual((revision.version, revision.change_type), (1, PolicyChange.CREATED))
        self.assertEqual((revision.actor, revision.reason), (self.admin, "Board decision"))
        self.assertEqual(revision.snapshot["Approvers"], "Reviewer")
        self.assertIn({"field": "Approvers", "before": "—", "after": "Reviewer"}, revision.changes)
        record = TestAdapter.audit_log[-1]
        self.assertEqual((record.resource_type, record.action), ("ApprovalPolicy", "created"))
        self.assertEqual(record.metadata["version"], 1)

    def test_saving_without_changes_records_nothing(self):
        self.save()
        self.save()
        self.assertEqual(len(self.history()), 1)

    def test_switching_a_rule_on_or_off_is_told_apart_from_other_edits(self):
        self.save()
        self.save(is_enabled=False, reason="Audit week")
        self.save(
            is_enabled=False,
            workflow_type=WorkflowType.SEQUENTIAL,
            approvers=[f"r:{self.role_reviewer.pk}", f"u:{self.admin.pk}"],
            sla_enabled=True,
            sla_value=4,
            sla_unit="hours",
            escalation=f"u:{self.admin.pk}",
        )
        latest, disabled, _ = self.history()
        self.assertEqual(disabled.change_type, PolicyChange.DISABLED)
        self.assertEqual(disabled.changes, [{"field": "Rule enabled", "before": "Yes", "after": "No"}])
        self.assertEqual((latest.version, latest.change_type), (3, PolicyChange.UPDATED))
        changes = {c["field"]: (c["before"], c["after"]) for c in latest.changes}
        self.assertEqual(changes["Workflow"], ("Single Approver", "Approval Flow"))
        self.assertEqual(changes["Approvers"], ("Reviewer", "Reviewer → Admin Test"))
        self.assertEqual(changes["SLA"], ("Off", "4 hours"))
        self.assertEqual(changes["Escalation backup"], ("—", "Admin Test"))

    def test_history_survives_deletion_and_continues_on_recreation(self):
        policy = self.save()
        policies.delete_policy(policy, by=self.admin, reason="Not needed")
        deleted = self.history()[0]
        self.assertEqual((deleted.change_type, deleted.policy), (PolicyChange.DELETED, None))
        self.assertEqual(deleted.snapshot["Approvers"], "Reviewer")
        self.assertEqual(TestAdapter.audit_log[-1].action, "deleted")
        self.save()
        self.assertEqual([r.version for r in self.history()], [3, 2, 1])

    def test_adding_a_scoped_trigger_records_creation_once(self):
        policies.add_scoped_policy(self.tenant, PUBLISH, "folder:general", by=self.admin)
        policies.add_scoped_policy(self.tenant, PUBLISH, "folder:general", by=self.admin)
        (revision,) = queries.policy_history(self.tenant, PUBLISH, "folder:general")
        self.assertEqual((revision.change_type, revision.snapshot["Approvers"]), (PolicyChange.CREATED, "—"))

    def test_requests_know_whether_their_rule_changed_since(self):
        self.save()
        req = self.raise_request()
        status = queries.policy_version_status(req)
        self.assertEqual((status.raised_under, status.current, status.changed_since), (1, 1, False))
        self.save(risk_level="high")
        self.assertTrue(queries.policy_version_status(req).changed_since)
