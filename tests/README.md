# The tests as a specification

Each module covers one area of the library. Its docstring explains the concept, and its test names state the rules.
Reading them in this order takes you from "what is an action" to "what a host must provide":

| Module | Covers |
|--------|--------|
| `test_defining_actions.py` | Putting an operation behind maker-checker: `@approval_action`, `ModelChangeHandler`, the registry |
| `test_configuring_rules.py` | Rules (policies): workflows, approvers, SLA, validation, and which rule applies to a change |
| `test_submitting.py` | Raising a request: what is captured, how stages are laid out, when submission is refused |
| `test_deciding.py` | Approving and rejecting, segregation of duties, multi-approver rules, approval flows, failures and retry |
| `test_cancelling_and_the_timeline.py` | Cancelling, comments, views, and who may open a request |
| `test_delegating.py` | Handing approvals to a colleague for a period |
| `test_sla_and_escalation.py` | Reminders, escalation to a backup approver, and the periodic task |
| `test_notifications.py` | Who is told about each event |
| `test_queues_and_display.py` | What host pages read: queues, filters, buttons, SLA status, stage progress |
| `test_rule_history.py` | Revisions of rules: versions, change types, audit records |
| `test_switching_approvals_off.py` | Turning maker-checker off for a tenant (`ApprovalsAdapter.is_enabled`) and back on |
| `test_host_contract.py` | Settings and the adapter a host provides, and the engine's dependency boundaries |

## The fixture world

`base.EngineTestCase` sets up one tenant with a maker, two Reviewer-role approvers and an admin, plus a user in a
second tenant (see its docstring). `tests/testapp` is a minimal host:

* `models.py`: tenants, roles holding permission codenames, members, and a `Document` to change.
* `adapter.py`: a complete `ApprovalsAdapter` that records notices in `TestAdapter.outbox` and audit records in
  `TestAdapter.audit_log`.
* `handlers.py`: one action written in each of the three styles. `publish_document` uses `@approval_action`,
  `DocumentEditHandler` uses `ModelChangeHandler`, and `FlakyHandler` is a plain `ActionHandler` that fails on demand.

Run the suite with `pytest`, or with tox (e.g. `tox -e py313-dj61`) to include the coverage gate. Set `DATABASE_URL` to run it on PostgreSQL.
