# Changelog

All notable changes are documented here. The project follows [Semantic Versioning](https://semver.org).

## Unreleased

## 0.4.0

First public release as `django-maker-checker` (import `django_maker_checker`, app label `approvals`).

- Supports Python 3.11+ and Django 5.2, 6.0 and 6.1.
- Celery is optional (`pip install "django-maker-checker[celery]"`). Without it, run the SLA pass with
  `python manage.py process_approval_slas`.
- New settings: `APPROVALS_CACHE_ALIAS` (cache for pending counts) and `APPROVALS_REFERENCE_PREFIX` (request reference
  prefix, default `REQ`).
- Ships type information (`py.typed`).
- Tested on PostgreSQL and SQLite.

### Upgrading from the private 0.x releases

Replace the dependency, then rename imports from the old package to `django_maker_checker` and the `INSTALLED_APPS`
entry to `"django_maker_checker"`. The app label, tables, migrations, settings and Celery task name are unchanged, so
no database migration is needed.

## 0.3.0 and earlier

Released privately before the project was open-sourced:

- **0.3.0**: `ApprovalsAdapter.is_enabled(tenant)` switches maker-checker on or off per tenant.
- **0.2.0**: rule history (`ApprovalPolicyRevision`), the version each request was raised under, and a read-only
  Django admin.
- **0.1.0**: approval rules per tenant and scope (single, multiple, sequential workflows; SLA and escalation; risk
  level), the request state machine, segregation of duties, delegation, the request timeline, the three ways of
  defining actions, and the read and policy APIs.
