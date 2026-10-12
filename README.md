# django-maker-checker

[![CI](https://github.com/joemash/django-maker-checker/actions/workflows/ci.yml/badge.svg)](https://github.com/joemash/django-maker-checker/actions/workflows/ci.yml)
[![Coverage](https://raw.githubusercontent.com/joemash/django-maker-checker/python-coverage-comment-action-data/badge.svg)](https://github.com/joemash/django-maker-checker/tree/python-coverage-comment-action-data)
[![PyPI](https://img.shields.io/pypi/v/django-maker-checker?label=pypi)](https://pypi.org/project/django-maker-checker/)
[![Python](https://img.shields.io/pypi/pyversions/django-maker-checker?label=python)](https://pypi.org/project/django-maker-checker/)
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

- Python 3.11+ and Django 5.2, 6.0 or 6.1, with `django.contrib.auth` and `django.contrib.contenttypes`
- [django-fsm-2](https://github.com/django-commons/django-fsm-2) (installed automatically)
- **PostgreSQL** is recommended for production. SQLite works for development and tests. On **MySQL / MariaDB** the
  "one open request per item" guarantee is not enforced, because those databases ignore Django's conditional unique
  constraints.
- Optional: Celery, to run the SLA job (`pip install "django-maker-checker[celery]"`). Without it, run a management
  command from cron.

---

## Quick start

A single-tenant project on Django's own users, groups and permissions needs no configuration:

```bash
pip install django-maker-checker
```

```python
# settings.py
INSTALLED_APPS = [..., "django.contrib.auth", "django.contrib.contenttypes", "django_maker_checker"]
```

Guard the function that makes the change, and register it from your app's `AppConfig.ready()`:

```python
from django_maker_checker.actions import approval_action


@approval_action(
    "credit_limit_change",
    label="Credit Limit Change",
    category="Accounts",
    lock_key=lambda p: f"account:{p['account_id']}",  # at most one open request per account
)
def change_credit_limit(*, tenant, maker, context, account_id, new_limit):
    Account.objects.filter(pk=account_id).update(credit_limit=new_limit)
```

Run `python manage.py migrate`, which also creates the permission `approvals.approve_credit_limit_change`. Grant it
to a group, then switch on a rule naming that group:

```python
from django_maker_checker import policies, services
from django_maker_checker.constants import Decision
from django_maker_checker.models import Tenant

policies.save_policy(
    policies.policy_for(Tenant.default(), "credit_limit_change"),
    policies.PolicySpec(approvers=[f"r:{risk_officers.pk}"]),
    by=admin_user,
)

outcome = change_credit_limit(
    tenant=Tenant.default(), maker=request.user, justification="Annual review", account_id=42, new_limit="5000.00"
)
outcome.pending  # True: nothing changed yet, and the risk officers were emailed

services.decide(outcome.approval_request.pk, risk_officer, Decision.APPROVE, "Within appetite")
# the credit limit changes now, recorded as the maker's change
```

A checker can also reject with a reason, or send the request back to the maker for changes. Rules can require one
approver, any N of several, all of them, or ordered steps, with an SLA and escalation.

**Multi-tenant, or your own roles and permissions?** Point `APPROVALS_TENANT_MODEL`, `APPROVALS_ROLE_MODEL` and
`APPROVALS_ADAPTER` at your models and an adapter class. See
[Integrating with your project](https://django-maker-checker.readthedocs.io/en/latest/integration.html).

## Documentation

**https://django-maker-checker.readthedocs.io**

- [Quick start](https://django-maker-checker.readthedocs.io/en/latest/quickstart.html)
- [Concepts](https://django-maker-checker.readthedocs.io/en/latest/concepts.html): the life cycle, who may decide,
  requesting changes, SLAs, the audit trail
- [Defining actions](https://django-maker-checker.readthedocs.io/en/latest/defining_actions.html) and
  [configuring rules](https://django-maker-checker.readthedocs.io/en/latest/rules.html)
- [Building a UI](https://django-maker-checker.readthedocs.io/en/latest/building_a_ui.html)
- [Settings](https://django-maker-checker.readthedocs.io/en/latest/settings.html) and the
  [API reference](https://django-maker-checker.readthedocs.io/en/latest/api.html)

## Contributing

Contributions are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md) and the
[development guide](https://django-maker-checker.readthedocs.io/en/latest/development.html). The tests are written as a specification of the
library: start with [tests/README.md](tests/README.md).

## License

[MIT](LICENSE)
