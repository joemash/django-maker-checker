# Changelog

All notable changes are documented here. The project follows [Semantic Versioning](https://semver.org).

## Unreleased

## 0.6.0

### Added

- **Locking several items per request.** `ActionHandler.lock_keys(payload)` (and the `lock_keys=` option of
  `@approval_action`) names every item a change touches, e.g. each transaction in a manual match. They are locked
  while the request is open, across all action types, so no other open request can claim any of them; locks are
  released when the request closes and taken again on retry or resubmission. New model `ApprovalLock`.
- `queries.open_requests_for(tenant, keys)` returns the open request holding each item, for marking items that are
  waiting for approval.
- **Action-specific rule settings.** `ActionHandler.validate_conditions(conditions)` reports problems with a rule's
  `conditions` through `PolicyError`, and `ActionHandler.describe_conditions(conditions)` describes them for rule
  history (`validate_conditions=` / `describe_conditions=` on `@approval_action`).

### Fixed

- Retrying a failed request while another open request holds the same `lock_key` raised a database error; it now
  gives the readable "already an open approval request" error.

### Upgrading

Run `migrate` (`approvals.0004`). Nothing changes for actions that don't define `lock_keys`.

## 0.5.0

### Added

- **Zero-configuration single-tenant setup.** `APPROVALS_TENANT_MODEL`, `APPROVALS_ROLE_MODEL` and
  `APPROVALS_ADAPTER` are now optional. Without them the engine uses a built-in, swappable tenant model
  (`approvals.Tenant`, one row), Django groups as roles, and the new built-in adapter
  (`django_maker_checker.auth_adapter.AuthApprovalsAdapter`): Django permissions in the `approvals` app (created
  after every `migrate`), email notifications and audit records on the `django_maker_checker.audit` logger.
- **N of M approvals.** Multiple Approvers rules take `required_approvals` (any N of the approvers). Listing a role
  more than once asks for that many different members of it.
- **Requesting changes.** `Decision.REQUEST_CHANGES` sends a request back to its maker with a note
  (status `changes_requested`; the item stays locked and the SLA pauses). The maker withdraws it or calls
  `services.resubmit()`, optionally with an amended payload, which starts a new round of approval.
  `queries.allowed_actions()` gains `resubmit`, and `permissions.can_resubmit()` is new.
- New notice kind `changes_requested`; new event types `changes_requested` and `resubmitted`.
- **Translations.** User-facing messages (errors, timeline entries, notifications, labels) are marked for translation,
  with an English catalog to translate from. Rule-history labels stay in English because revisions store them.

### Changed

- A request is refused at submission unless enough people other than the maker could give the approvals each step
  needs. Previously a "require all" rule that listed the maker passed this check and could never complete.
- `actions.to_payload` now lives in `registry` (still importable from `actions`).
- Wording: the "not enough approvers" error uses proper plurals, and a one-minute SLA reads "1 minute".

### Upgrading

Run `migrate` (`approvals.0003`). Projects that set their own tenant model are unaffected by the built-in tenant: it is
swapped out and gets no table.

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
