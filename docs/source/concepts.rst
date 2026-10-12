Concepts
========

Terms
-----

Maker
   The user who performs the guarded action.

Checker
   The user who approves or rejects. Never the maker of the same request, whatever their permissions.

Approver
   A user, or every member of a role, named on a rule as allowed to act as checker. They must hold the action's
   ``approve_<action_type>`` permission.

Approval policy (rule)
   The rule for one action type in one tenant: workflow, approvers, SLA, escalation backup and risk level. It applies
   tenant-wide (``scope_key == ""``) or to one host-defined scope, such as a department. A scoped rule wins over the
   tenant-wide one.

Approval request
   One maker's proposed change, with its payload, a snapshot of the item before the change, the maker's justification
   and a snapshot of the rule it was raised under.

Delegation
   A time-boxed hand-over: the delegate acts with the delegator's authority, and decisions record both people.

Workflows
---------

==================  ==========================================================================
Workflow            Approval needed
==================  ==========================================================================
Single Approver     One named user or role
Multiple Approvers  Any one of several, any N of them (``required_approvals``), or all of
                    them (``require_all``)
Approval Flow       Ordered steps; each step is decided before the next one starts
==================  ==========================================================================

Each approver is a *slot* that one person fills. A role listed twice therefore needs two different members of it,
which is how "two people from Finance" is written.

Each request is split into *stages*: Single and Multiple Approvers have one stage, and an Approval Flow has one stage
per step. Only the current stage's approvers can decide.

Life cycle
----------

.. code-block:: text

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
         CANCELLED ◄── cancel ── CHANGES_REQUESTED ◄── request changes (note required)
                                      │        └── resubmit (new round) ──► PENDING
                                      │ approve (each stage / approver)
                                      ▼
                                  APPROVED ──► handler.validate() + handler.execute()
                                      │                    │
                                      ▼                    ▼ exception
                                  EXECUTED              FAILED ──(retry, admin)──► APPROVED

- **Status is a protected state machine** (django-fsm-2): only the transition methods change it.
- **Rules are snapshotted.** Each request copies its rule when it is raised, so editing or deleting a rule never
  changes requests already in flight.
- **One open request per item**, enforced by a partial unique index on ``(tenant, action_type, lock_key)``. A second
  submission for the same item is refused with the reference of the open one. Actions that touch several items lock
  all of them, across action types (``lock_keys``).
- **Execution is separate from the decision.** If applying the change fails, the approval stands and the request
  becomes ``FAILED`` with the error. An administrator can retry it.
- **Every write locks the row** (``select_for_update``), so two checkers approving at once can't apply a change twice.
  Notifications are sent only after the transaction commits.

Who may decide
--------------

:func:`django_maker_checker.permissions.decision_slot` is the single source of truth. A user may decide when they:

1. are an active member of the request's tenant;
2. are not the maker (there is no superuser or staff exception);
3. have not acted on this request already in this round, so one person can't approve two steps;
4. hold ``approve_<action_type>``;
5. fill an open slot on the **current** stage: they are the named user, hold the named role, or are the escalation
   approver.

A delegate satisfies 4 and 5 through the delegator. Submission is refused up front when no one other than the maker
could approve a stage, so a request can never wait for someone who can't decide it.

Requesting changes
------------------

Besides approving and rejecting, a current approver can send a request back to its maker with
``Decision.REQUEST_CHANGES`` and a note saying what to change. While the request is with the maker, nobody can
decide it, its SLA is paused, and it keeps its lock on the item. The maker then either withdraws it with
:func:`~django_maker_checker.services.cancel`, or calls :func:`~django_maker_checker.services.resubmit`, optionally
with an amended payload for the same item.

Resubmission starts a new *round*: the item is snapshotted again, approval restarts from the first step with a fresh
SLA, and approvals from the earlier round no longer count, because they approved a different change. Everyone,
including the approver who asked for changes, may decide again.

Delegation
----------

:func:`django_maker_checker.services.delegate` hands an approver's authority to a colleague for a window, optionally
for some action types only. Only approvers can delegate, the delegate must belong to the same tenant, and a delegation
can be ended early with :func:`~django_maker_checker.services.revoke_delegation`. Decisions made under it record
both the delegate and the person they acted for.

SLA and escalation
------------------

When a rule has an SLA, each stage gets its own window, starting when the stage starts.
:func:`django_maker_checker.services.process_sla`:

- reminds the current approvers once 75% of the window has passed, and
- when the window has passed, adds the rule's escalation backup as an approver on the stage, who may then approve on
  their own. Without a backup, the current approvers are reminded instead.

Each happens once per stage. The job is safe to run concurrently and repeatedly; schedule it every few minutes,
either as the Celery task ``approvals.tasks.process_approval_slas`` (needs the ``celery`` extra) or from cron:

.. code-block:: text

   */5 * * * *  python manage.py process_approval_slas

Audit trail
-----------

``ApprovalEvent`` rows form each request's timeline: submitted, notified, viewed, SLA warning, escalated, step
approved, approved, rejected, cancelled, comment, executed, failed and retried. Every transition and delegation
change is also passed to your adapter's ``audit()`` as an :class:`~django_maker_checker.adapter.AuditRecord`.

Rule history
------------

Changing a rule changes the control itself, so rules are versioned. Every create, update, enable/disable and delete
made through :mod:`~django_maker_checker.policies` writes an append-only ``ApprovalPolicyRevision`` in the same
transaction. A revision holds:

- the version and change type,
- a field-by-field before/after list in display form, for example ``SLA: Off → 4 hours``,
- the full rule after the change,
- who made the change, and an optional reason.

History is kept per tenant, action and scope, so it survives deleting the rule, and versions continue if it is
created again. Each request records the version it was raised under;
:func:`~django_maker_checker.queries.policy_version_status` tells a UI whether the rule has changed since.
