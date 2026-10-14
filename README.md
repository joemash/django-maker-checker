# django-maker-checker

[![CI](https://github.com/joemash/django-maker-checker/actions/workflows/ci.yml/badge.svg)](https://github.com/joemash/django-maker-checker/actions/workflows/ci.yml)
[![Coverage](https://raw.githubusercontent.com/joemash/django-maker-checker/python-coverage-comment-action-data/badge.svg)](https://github.com/joemash/django-maker-checker/tree/python-coverage-comment-action-data)
[![PyPI](https://img.shields.io/pypi/v/django-maker-checker?label=pypi)](https://pypi.org/project/django-maker-checker/)
[![Python](https://img.shields.io/pypi/pyversions/django-maker-checker?label=python)](https://pypi.org/project/django-maker-checker/)
[![Docs](https://readthedocs.org/projects/django-maker-checker/badge/?version=latest)](https://django-maker-checker.readthedocs.io)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)


**Not every change should take effect immediately.**

`django-maker-checker` is an open-source, headless maker-checker (four-eyes) approval engine for Django. It lets you require a second authorized person to approve sensitive changes before they take effect.

In financial and enterprise systems, a user might need to change a credit limit, create a privileged user, modify account details, or initiate another sensitive operation. You need more than an approval button: you need enforceable separation of duties, configurable policies, controlled execution and an audit trail.

`django-maker-checker` provides reusable infrastructure for these controls, so you don't have to build a separate approval mechanism for every Django application.

## Why django-maker-checker?

- **Changes are held until approval.** A guarded action becomes an approval request rather than taking effect immediately. On approval, the action executes against the current state of the data; if that state has changed, the request fails instead of silently overwriting newer changes.
- **Separation of duties is enforced.** The maker cannot approve their own request, including through privileged-user or delegation paths. A person cannot approve multiple steps of the same request and a request cannot be raised if no eligible checker exists.
- **Approval rules are data, not code.** Configure which actions require approval, who can approve them and how approval steps work without deploying code for every policy change. Policy changes are versioned with who made them, when, and why.
- **Approvals have operational controls.** Configure SLAs, reminders, and escalation to backup approvers so pending requests don't depend entirely on someone remembering to check a queue.
- **Decisions are auditable.** Requests have a timeline, decisions are recorded, and events can be passed to your audit log.
- **Concurrent decisions are controlled.** Decisions lock the relevant row and only one open request may exist per item, subject to the database requirements below.
- **Your UI stays yours.** The library provides models, rules, services, and read queries without imposing a frontend or admin interface.

## Who is it for?

`django-maker-checker` is designed for Django applications where sensitive operations need an independent approval step.

| Use case | Example |
|---|---|
| Fintech and financial systems | Changing credit limits or payment instructions |
| User administration | Creating or deleting privileged users |
| Accounting and back-office systems | Approving sensitive account or configuration changes |
| Multi-tenant SaaS | Letting each tenant define its own approval policies |
| Enterprise applications | Requiring independent authorization for sensitive operations |

These are examples of workflows the library can help implement; each action must still be integrated and configured by the application.

## How it works

The typical lifecycle is straightforward:

1. **Define an action.** Mark a sensitive operation as requiring approval when its policy says so.
2. **Submit a request.** A maker initiates the action. Instead of applying the change immediately, the library creates an approval request.
3. **Evaluate the policy.** The request is routed to eligible checkers according to the configured rules.
4. **Review the request.** A checker can approve, reject, or request changes, depending on the workflow.
5. **Apply the change.** Once the approval requirements are satisfied, the action executes against the current data state.
6. **Record the outcome.** The request's decisions and events remain available for auditing.

The approval engine handles the workflow; your application remains responsible for defining the business operation and building the user experience.

## How it compares

| Capability | django-maker-checker | Workflow engines (e.g. django-approval-workflow) | Moderation libraries (e.g. django-approval) |
|---|---|---|---|
| Primary purpose | Approval-gated execution of sensitive actions | Orchestrating workflows across steps | Reviewing proposed object edits |
| Approval policy | Tenant-configurable rules | Typically defined per workflow | Typically defined per model or workflow |
| Separation of duties | Enforced by the approval engine | Depends on workflow configuration | Depends on implementation and permissions |
| SLAs and escalation | Built in | May require additional scheduling and escalation logic | Depends on the library |
| Multi-tenancy | Supported through configurable integration points | Depends on the engine | Depends on the library |
| User interface | Headless | Depends on the engine | Depends on the library |

Choose a broader workflow engine if you need complex orchestration, such as parallel branches, organizational hierarchies, or rich process routing.

Choose `django-maker-checker` when your primary requirement is to ensure that **sensitive changes cannot be made by one person acting alone**, with approval rules and decisions that can be audited.

## Requirements

- Python 3.11+
- Django 5.2, 6.0, or 6.1
- `django.contrib.auth` and `django.contrib.contenttypes`
- [django-fsm-2](https://github.com/django-commons/django-fsm-2), installed automatically

**Database:** PostgreSQL is recommended for production. SQLite works for development and tests. On MySQL/MariaDB, the guarantee that only one open request exists per item is not enforced because these databases ignore Django's conditional unique constraints.

**Background jobs:** Celery is optional. It can run the SLA job; alternatively, schedule the relevant management command with cron.

Install the Celery integration with:

```bash
pip install "django-maker-checker[celery]"
```

## Quick start

### 1. Install the package

```bash
pip install django-maker-checker
```

### 2. Add it to Django

A single-tenant project using Django's built-in users, groups, and permissions requires no additional integration configuration.

```python
# settings.py

INSTALLED_APPS = [
    # ...
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django_maker_checker",
]
```

### 3. Define an approval-gated action

Suppose you want credit-limit changes to require approval.

```python
from django_maker_checker.actions import approval_action


@approval_action(
    "credit_limit_change",
    label="Credit Limit Change",
    category="Accounts",
    lock_key=lambda p: f"account:{p['account_id']}",
)
def change_credit_limit(
    *,
    tenant,
    maker,
    context,
    account_id,
    new_limit,
):
    Account.objects.filter(pk=account_id).update(
        credit_limit=new_limit
    )
```

The `lock_key` identifies the account associated with the request, allowing the library to prevent multiple open requests for the same item when the database supports the required constraint.

Register the action from your application's `AppConfig.ready()`.

### 4. Configure an approval policy

Run the migrations:

```bash
python manage.py migrate
```

The migrations also create the permission `approvals.approve_credit_limit_change`. Grant it to the appropriate group, then configure the policy to use that group.

```python
from django_maker_checker import policies, services
from django_maker_checker.constants import Decision
from django_maker_checker.models import Tenant


policies.save_policy(
    policies.policy_for(
        Tenant.default(),
        "credit_limit_change",
    ),
    policies.PolicySpec(
        approvers=[f"r:{risk_officers.pk}"],
    ),
    by=admin_user,
)
```

### 5. Submit and approve a request

When a maker initiates the action, the change is held pending approval.

```python
outcome = change_credit_limit(
    tenant=Tenant.default(),
    maker=request.user,
    justification="Annual review",
    account_id=42,
    new_limit="5000.00",
)

assert outcome.pending  # The credit limit has not changed yet.
```

An eligible checker can approve the request:

```python
services.decide(
    outcome.approval_request.pk,
    risk_officer,
    Decision.APPROVE,
    "Within appetite",
)
```

Once the approval requirements are satisfied, the action executes and the resulting change is recorded.

The same workflow supports rejection with a reason and requesting changes from the maker.

**Note:** This example illustrates the core API. Your application must supply its own `Account` model, user and group setup, action registration, and approval interface.

## Configurable approval workflows

Approval policies can express different requirements, including:

- A single approver.
- Any N of several eligible approvers.
- Approval from all required approvers.
- Ordered approval steps.
- SLAs, reminders and escalation to backup approvers.

Policies are stored as data, allowing authorized administrators to adjust approval requirements without deploying application code for every policy change.

## Integrating with your application

The library is headless by design. It doesn't force you to use a particular frontend, admin interface, or API framework.

For multi-tenant applications or projects with custom roles and permissions, configure the relevant integration points:

- `APPROVALS_TENANT_MODEL`
- `APPROVALS_ROLE_MODEL`
- `APPROVALS_ADAPTER`

See the [integration guide](https://django-maker-checker.readthedocs.io/en/latest/integration.html).

## Documentation

**Full documentation:** https://django-maker-checker.readthedocs.io

- [Quick start](https://django-maker-checker.readthedocs.io/en/latest/quickstart.html)
- [Concepts and lifecycle](https://django-maker-checker.readthedocs.io/en/latest/concepts.html)
- [Defining actions](https://django-maker-checker.readthedocs.io/en/latest/defining_actions.html)
- [Configuring approval rules](https://django-maker-checker.readthedocs.io/en/latest/rules.html)
- [Building a UI](https://django-maker-checker.readthedocs.io/en/latest/building_a_ui.html)
- [Settings](https://django-maker-checker.readthedocs.io/en/latest/settings.html)
- [API reference](https://django-maker-checker.readthedocs.io/en/latest/api.html)
- [Development guide](https://django-maker-checker.readthedocs.io/en/latest/development.html)

## Contributing

Contributions are welcome! Whether you're improving the API, reporting edge cases, adding integrations, or strengthening the test suite, your input is valuable.

- Read [CONTRIBUTING.md](CONTRIBUTING.md) for contribution guidelines.
- Follow the [development guide](https://django-maker-checker.readthedocs.io/en/latest/development.html) to set up your environment.
- Start with [tests/README.md](tests/README.md), which describes the test suite and how it specifies expected library behaviour.
- Open an issue to discuss substantial changes before submitting a pull request.

Bug reports, use cases, design feedback, and pull requests are all appreciated.

## License

Released under the [MIT License](LICENSE).
