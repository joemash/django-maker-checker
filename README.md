# django-maker-checker

[![CI](https://github.com/joemash/django-maker-checker/actions/workflows/ci.yml/badge.svg)](https://github.com/joemash/django-maker-checker/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/django-maker-checker.svg)](https://pypi.org/project/django-maker-checker/)
[![Python](https://img.shields.io/pypi/pyversions/django-maker-checker.svg)](https://pypi.org/project/django-maker-checker/)
[![Docs](https://readthedocs.org/projects/django-maker-checker/badge/?version=latest)](https://django-maker-checker.readthedocs.io)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**Documentation: https://django-maker-checker.readthedocs.io**

**Maker-checker (four-eyes) approval of sensitive changes for Django.**

When a user (the *maker*) performs a guarded action, such as creating a user, changing a credit limit or approving a
payment run, the change is **not applied**. It becomes an approval request that someone else (a *checker*) must
approve, under rules each tenant configures. Only then is the change applied, re-checked against the current state of
the data.

It is built for the controls that regulated businesses (banks, fintechs, back offices) are audited on:

- **The change is held, not just flagged.** The action is captured and applied only on approval. If the data changed
  meanwhile, the request fails instead of overwriting someone else's edit.
- **Segregation of duties is enforced.** A maker can never approve their own request, not as a superuser and not
  through a delegation. One person can't approve two steps. A request can't be raised if nobody else could approve it.
- **Rules are data, not code.** Each tenant decides which actions need approval, by whom (users or roles), in what
  workflow, and how fast, without a deploy. Every rule change is versioned with who, when and why.
- **Nothing gets stuck.** Each step has an SLA with a reminder and escalation to a backup approver.
- **Everything is on the record.** Every request has a timeline, every decision is stored, and every event is passed to
  your audit log.
- **Concurrency-safe.** Decisions lock the row, and only one open request may exist per item.

It is **headless**: it ships models, rules, services and read queries, and no UI. You build the screens your product
needs on its Python API.

### How it compares

| | django-maker-checker | Workflow engines (e.g. django-approval-workflow) | Moderation (e.g. django-approval) |
|---|---|---|---|
| What is approved | A pending change, applied on approval | An existing object moving through steps | An edit to a model, kept in a sandbox |
| Who decides what needs approval | Tenant-configured rules (data) | Code, per flow | Code, per model |
| Maker can't approve own change | Enforced | — | Privileged users bypass by default |
| SLA and escalation | Built in | Tracked; you run the timeout loop | — |
| Multi-tenant | Built in | — | — |

If you need rich routing (quorums, org-chart hierarchies, parallel tracks), a workflow engine is the better fit. If you
need "nobody changes this alone, and we can prove it", this is.

---

## Requirements

- Python 3.11+ and Django 5.2, 6.0 or 6.1
- [django-fsm-2](https://github.com/django-commons/django-fsm-2) (installed automatically)
- **PostgreSQL** is recommended for production. SQLite works for development and tests. On **MySQL / MariaDB** the
  "one open request per item" guarantee is not enforced, because those databases ignore Django's conditional unique
  constraints.
- Optional: Celery, to run the SLA job (`pip install "django-maker-checker[celery]"`). Without it, run a management
  command from cron.

---

## Quick start

### 1. Install

```bash
pip install django-maker-checker
```

### 2. Configure

```python
# settings.py
INSTALLED_APPS = [
    ...,
    "django_maker_checker",
    "approvals_integration",  # your app holding the adapter and guarded actions
]

APPROVALS_TENANT_MODEL = "accounts.Organisation"   # who owns rules and requests
APPROVALS_ROLE_MODEL = "accounts.Role"             # a role that can be named as an approver
APPROVALS_ADAPTER = "approvals_integration.adapter.Approvals"
```

The engine's app label is `approvals` (tables `approvals_*`). Run `python manage.py migrate`.

### 3. Tell the engine about your users

The engine never imports your code. Everything it needs to know about tenants, roles, permissions, notifications and
auditing goes through one adapter class. For a project where each user has a `Membership` in one organisation with
one role:

```python
# approvals_integration/adapter.py
from django.contrib.auth import get_user_model
from django.db.models import Q

from django_maker_checker.adapter import ApprovalsAdapter
from accounts.models import Membership, Role


class Approvals(ApprovalsAdapter):
    def _membership(self, user):
        return Membership.objects.filter(user=user, active=True).select_related("organisation", "role").first()

    def tenant_of(self, user):
        membership = self._membership(user)
        return membership.organisation if membership and user.is_active else None

    def members(self, tenant, user_ids=None, role_ids=None):
        users = get_user_model().objects.filter(membership__organisation=tenant, membership__active=True, is_active=True)
        if user_ids is not None or role_ids is not None:
            users = users.filter(Q(pk__in=user_ids or ()) | Q(membership__role_id__in=role_ids or ()))
        return users.distinct()

    def role_ids_of(self, user):
        membership = self._membership(user)
        return {membership.role_id} if membership and membership.role_id else set()

    def roles(self, tenant):
        return Role.objects.filter(organisation=tenant)

    def role_member_count(self, role):
        return Membership.objects.filter(role=role, active=True).count()

    def has_permission(self, user, codename):
        return user.has_perm(f"accounts.{codename}")

    def role_has_permission(self, role, codename):
        return role.permissions.filter(codename=codename).exists()

    def notify(self, notice):
        ...  # send notice.text to notice.recipients by email, in-app, chat

    def audit(self, record):
        ...  # write the AuditRecord to your audit log
```

### 4. Guard an action

```python
# approvals_integration/actions.py
from django_maker_checker.actions import approval_action


@approval_action(
    "credit_limit_change",
    label="Credit Limit Change",
    category="Accounts",
    lock_key=lambda p: f"account:{p['account_id']}",  # at most one open request per account
    target_label=lambda p: f"Account {p['account_id']}",
)
def change_credit_limit(*, tenant, maker, context, account_id, new_limit):
    Account.objects.filter(pk=account_id, organisation=tenant).update(credit_limit=new_limit)
    return {"account_id": account_id}
```

Import the module from your app's `AppConfig.ready()` so the action is registered at start-up. Then call it like the
function it is:

```python
outcome = change_credit_limit(
    tenant=request.user.membership.organisation,
    maker=request.user,
    request=request,
    justification="Annual review",
    account_id=42,
    new_limit="5000.00",
)
if outcome.pending:
    messages.info(request, f"Sent for approval as {outcome.approval_request.reference}.")
```

With no rule configured for the tenant the function simply runs, so adding maker-checker changes nothing until a tenant
switches it on.

### 5. Switch on a rule and approve

```python
from django_maker_checker import policies, services
from django_maker_checker.constants import Decision, WorkflowType

rule = policies.policy_for(organisation, "credit_limit_change")
policies.save_policy(
    rule,
    policies.PolicySpec(workflow_type=WorkflowType.SINGLE, approvers=[f"r:{risk_officer_role.pk}"]),
    by=admin_user,
    reason="New credit policy",
)

# later, a risk officer (not the maker) approves; the credit limit changes now
services.decide(approval_request.pk, risk_officer, Decision.APPROVE, "Within appetite")
```

Approvers must hold the action's permission, `approve_credit_limit_change` here. The engine names the permission; you
create and grant it (see [Permissions](#permissions)).

---

## Concepts

| Term | Meaning |
|---|---|
| **Maker** | The user who performs the guarded action. |
| **Checker** | The user who approves or rejects. Never the maker of the same request, whatever their permissions. |
| **Approver** | A user, or every member of a role, named on a rule as allowed to act as checker. They must hold `approve_<action_type>`. |
| **Approval Policy (rule)** | The rule for one action type in one tenant: workflow, approvers, SLA, escalation backup and risk level. It applies tenant-wide (`scope_key == ""`) or to one host-defined scope (say, a department); a scoped rule wins. |
| **Approval Request** | One maker's proposed change. `PENDING → APPROVED → EXECUTED`, or `REJECTED` (with a reason), `CANCELLED`, or `FAILED` (approved, but applying it raised an error; it can be retried). |
| **Delegation** | A time-boxed hand-over: the delegate acts with the delegator's authority, and decisions record both people. |

Workflows:

| Workflow | Approval needed |
|---|---|
| Single Approver | One named user or role |
| Multiple Approvers | Any one of several, or all of them (`require_all`) |
| Approval Flow | Ordered steps; each step is decided before the next starts |

## How a request flows

```
 Maker performs action
        │
        ▼
 services.submit_or_execute(action_type, tenant=…, maker=…, payload=…)
        │
        ├── no enabled, configured rule ──► handler.execute() now
        │
        └── rule applies ──► ApprovalRequest(PENDING) + stages + events; approvers notified
                                   │
          ┌────────────── cancel ──┼── reject (reason required) ──► REJECTED
          ▼                        │
      CANCELLED                    │ approve (each stage / approver)
                                   ▼
                               APPROVED ──► handler.validate() + handler.execute()
                                   │                    │
                                   ▼                    ▼ exception
                               EXECUTED              FAILED ──(retry, admin)──► APPROVED
```

- **Status is a protected state machine** (django-fsm-2): only transition methods change it.
- **Rules are snapshotted.** Each request copies its rule when it is raised, so editing or deleting a rule never
  changes requests already in flight.
- **One open request per item**, by a partial unique index on `(tenant, action_type, lock_key)`.
- **Execution is separate from the decision.** If applying fails, the approval stands and the request is `FAILED`.
- **Every write locks the row** (`select_for_update`), so two checkers approving at once can't apply a change twice.
  Notifications are sent after commit.

### Who may decide

`permissions.decision_slot(user, request)` is the single source of truth. The user must:

1. be an active member of the request's tenant;
2. not be the maker (no superuser or staff exception);
3. not have acted on this request already;
4. hold `approve_<action_type>`;
5. fill an open slot on the **current** stage: they are the named user, hold the named role, or are the escalation
   approver.

A delegate satisfies 4 and 5 through the delegator.

### SLA and escalation

`services.process_sla()` sends one reminder at 75% of a step's window. When the window has passed, it adds the rule's
escalation backup as an approver on the step, once. It is safe to run concurrently and repeatedly. Schedule it every
few minutes, either:

- with Celery beat: the task `approvals.tasks.process_approval_slas` (needs the `celery` extra), or
- with cron: `python manage.py process_approval_slas`.

### Audit trail

`ApprovalEvent` rows form each request's timeline: submitted, notified, viewed, SLA warning, escalated, step approved,
approved, rejected, cancelled, comment, executed, failed, retried. Every transition, delegation change and rule change
is also passed to `adapter.audit()` as an `AuditRecord`.

### Rule history

Changing a rule changes the control itself, so rules are versioned. Every create, update, enable/disable and delete
through `policies` writes an append-only `ApprovalPolicyRevision` in the same transaction: the version, the change
type, a field-by-field before/after (`SLA: Off → 4 hours`), the full rule, who made it and why. History survives
deletion, and each request records the version it was raised under (`queries.policy_version_status(request)` tells a
UI whether the rule has changed since).

---

## Integrating with your project

### The adapter

| Method | Answers |
|---|---|
| `tenant_of(user)` | The tenant the user acts for, or None if they are not an active member. |
| `members(tenant, user_ids=None, role_ids=None)` | Active users of the tenant, optionally only those with the given ids or roles. |
| `role_ids_of(user)` / `roles(tenant)` / `role_member_count(role)` | Role membership, and the roles that can be picked as approvers. |
| `has_permission(user, codename)` / `role_has_permission(role, codename)` | Your permission check. |
| `notify(notice)` | Deliver a `Notice` (requested, decided, escalated, cancelled, SLA warning). Called after commit; failures are logged and never undo a decision. |
| `audit(record)` | Write an `AuditRecord` to your audit log. |
| `is_enabled(tenant)` | Optional. Whether maker-checker is switched on for the tenant (default `True`). |
| `scope_label`, `request_url`, `user_label`, `role_label` | Optional presentation helpers. |

### Permissions

The engine only names the permission codenames it checks: `approve_<action_type>` for each action
(`ActionHandler.approve_permission`) and `permissions.MANAGE_POLICIES` for administrators. Your project creates and
grants them, for example with a `post_migrate` handler that loops over `registry.all_handlers()`.

### Switching maker-checker on per tenant

Override `is_enabled(tenant)` to put the feature behind a feature flag, a plan or a setting. While it returns False the
tenant is treated as having no rules: guarded actions run immediately, `requires_approval` is False, `submit` refuses,
and the SLA job skips the tenant. Rules, history and requests are kept, so switching back on resumes where it stopped.
Requests that are open at the switch stay open and can still be cancelled.

### Migration order

Django resolves `APPROVALS_TENANT_MODEL` and `APPROVALS_ROLE_MODEL` to their app's *first* migration. If those models
are created in a later migration, add an empty migration to your integration app that depends on it and runs before
the engine:

```python
class Migration(migrations.Migration):
    dependencies = [("accounts", "0007_role")]
    run_before = [("approvals", "0001_initial")]
    operations = []
```

As with `AUTH_USER_MODEL`, choose the tenant and role models before the first migration; changing them later is not
supported.

---

## Defining actions

Whether an action needs approval is data. Code only describes how the action is captured and applied. Pick the
lightest option that fits:

| The action is… | Use | You write |
|---|---|---|
| A service function you can call with plain arguments | `@approval_action` (`actions`) | A decorator |
| A change to some fields of one model record | `ModelChangeHandler` (`model_change`) | A subclass naming the model and fields |
| Anything else: files, many records, external systems | `ActionHandler` (`registry`) | A full handler class |

In every case each action is one handler registered under its `action_type`, the call site is the same whether or not
a rule applies, and payloads must survive a JSON round trip (`Decimal` and dates arrive as strings).

### `@approval_action`: defer a function call

```python
@approval_action(
    "balance_update",
    label="Balance Update",
    category="Balances",
    lock_key=lambda p: f"{p['account_id']}:{p['balance_date']}",
    target_label=lambda p: f"Account {p['account_id']} on {p['balance_date']}",
    snapshot_before=lambda tenant, p: {"new_balance": current_balance(tenant, p)},  # "Current" in the review panel
    scopes=(Scope.GLOBAL, Scope.SCOPED),
    scope_keys=lambda tenant, p: [f"department:{department_of(p['account_id'])}"],
    matches=lambda policy, p: amount_above(policy.conditions.get("amount_gt"), p),  # optional thresholds
)
def update_balance(*, tenant, maker, context, account_id, balance_date, new_balance):
    ...  # runs as the maker's change; context.actor is the final checker
```

`update_balance.apply(...)` calls the undecorated function, for tests and admin tools that must bypass approval.

### `ModelChangeHandler`: defer a record edit

```python
@register
class CreditLimitChangeHandler(ModelChangeHandler):
    action_type = "credit_limit_change"
    label = "Credit Limit Change"
    category = "Accounts"
    model = Account
    fields = ("credit_limit",)      # nothing else can change through this action
    tenant_field = "organisation"   # the model's foreign key to the tenant (default "tenant")


outcome = CreditLimitChangeHandler.submit_change(account, {"credit_limit": "5000.00"}, maker=request.user)
```

The base class takes care of the lock per record, the snapshot of the changed fields, an optimistic check that the
record hasn't changed since submission, field conversion, validation and save, and Current/Proposed review rows.

### `ActionHandler`: full control

```python
@register
class PaymentRunHandler(ActionHandler):
    action_type = "payment_run"
    label = "Payment Run"
    category = "Payments"

    def lock_key(self, payload): ...                        # required: identifies the item
    def target_label(self, payload): ...
    def snapshot_before(self, tenant, payload): ...
    def scope_keys(self, tenant, payload): ...              # for scoped rules, most specific first
    def matches(self, policy, payload): ...                 # thresholds from policy.conditions
    def validate(self, approval_request): ...               # re-checked right before applying
    def execute(self, approval_request, context): ...       # apply; return a JSON-safe result
    def review_context(self, approval_request): ...         # data for your review panel
```

Call it with `services.submit_or_execute(action_type, tenant=…, maker=…, payload=…, justification=…, request=…)`.
`execute()` runs in a transaction: raise to roll back and record `FAILED`, queue background work with
`transaction.on_commit`, and make writes to external systems safe to retry.

---

## Building a UI

`queries` returns everything an approvals page needs, already filtered by what the viewer may see:

| Function | For |
|---|---|
| `awaiting(user, filter)` | Requests waiting for the user's decision now |
| `made_by(user, open_only, filter)` | The user's own requests |
| `checker_history(user, filter)` | Decided requests the user may review |
| `pending_count(user)` | A badge (cached per tenant) |
| `allowed_actions(user, request)` | Which buttons to show |
| `sla_status(request)` / `stage_progress(request)` | Progress bars and step strips |
| `policy_history(tenant, action_type)` | A rule's revisions |

`RequestFilter` filters by status, action, risk, maker, dates and free text. Each handler's `review_template` and
`review_context()` describe its review panel.

## Public API

| Module | Use it for |
|---|---|
| `services` | Writes: `submit_or_execute`, `submit`, `decide`, `cancel`, `retry`, `add_comment`, `record_view`, `delegate`, `revoke_delegation`, `process_sla`, `find_policy`, `requires_approval` |
| `policies` | Rules: `policy_for`, `save_policy`, `add_scoped_policy`, `delete_policy`, `describe`, `current_version` (invalid input raises `PolicyError` with displayable messages) |
| `queries` | Reads for UIs (above) |
| `permissions` | `can_decide`, `can_cancel`, `can_retry`, `can_view`, `can_manage_policies`, `approver_choices` |
| `registry`, `actions`, `model_change` | Defining actions |
| `models` | Read-only access; change state only through `services` and `policies` |
| `adapter` | `ApprovalsAdapter`, `Notice`, `AuditRecord` |

To react to outcomes, use `django_fsm.signals.post_transition` with `sender=ApprovalRequest` (it fires inside the
transaction, before the save, so use `transaction.on_commit`), or `post_save` on `ApprovalEvent`.

## Settings

| Setting | Default | |
|---|---|---|
| `APPROVALS_TENANT_MODEL` | required | `"app_label.Model"` of the tenant |
| `APPROVALS_ROLE_MODEL` | required | `"app_label.Model"` of a role that can be named as an approver |
| `APPROVALS_ADAPTER` | required | Dotted path of your `ApprovalsAdapter` subclass |
| `APPROVALS_CACHE_ALIAS` | `"default"` | Cache for pending counts |
| `APPROVALS_CACHE_TIMEOUT` | `60` | Seconds a pending count is cached |
| `APPROVALS_REFERENCE_PREFIX` | `"REQ"` | Prefix of request references, as in `REQ-2610-00042` |

## Known limits

- No built-in condition language: thresholds go in `ApprovalPolicy.conditions` and are read by `ActionHandler.matches()`.
- Approval strategies are one, any one of several, all, or ordered steps. There are no N-of-M quorums or org-chart
  routing yet.
- Messages are in English and not yet translatable.

## Contributing

Contributions are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md) and the
[development guide](https://django-maker-checker.readthedocs.io/en/latest/development.html). The tests are written as a specification of the
library: start with [tests/README.md](tests/README.md).

## License

[MIT](LICENSE)
