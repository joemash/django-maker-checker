Configuring rules
=================

Rules (``ApprovalPolicy``) belong to a tenant and an action type. Your administration screens build a
:class:`~django_maker_checker.policies.PolicySpec`, typically from a form, and call
:func:`~django_maker_checker.policies.save_policy`, which validates it, saves it and records a revision. All
validation lives there, so every screen and API enforces the same rules.

.. code-block:: python

   from django_maker_checker import policies
   from django_maker_checker.constants import SlaUnit, WorkflowType

   rule = policies.policy_for(tenant, "payment_run")      # the saved rule, or an unsaved one with defaults
   try:
       policies.save_policy(
           rule,
           policies.PolicySpec(
               workflow_type=WorkflowType.SEQUENTIAL,
               approvers=[f"r:{finance_role.pk}", f"u:{cfo.pk}"],   # step 1: any finance member; step 2: the CFO
               sla_enabled=True,
               sla_value=4,
               sla_unit=SlaUnit.HOURS,
               escalation=f"u:{deputy_cfo.pk}",
               risk_level="high",
           ),
           by=request.user,
           reason="Quarter-end control",
       )
   except policies.PolicyError as error:
       form.add_error(None, error.messages)   # readable, ready to show

PolicySpec
----------

.. list-table::
   :header-rows: 1
   :widths: 25 15 60

   * - Field
     - Default
     - Meaning
   * - ``workflow_type``
     - ``"single"``
     - ``"single"``, ``"multi"`` or ``"sequential"`` (Approval Flow)
   * - ``approvers``
     - ``[]``
     - Tokens: ``u:<user id>`` or ``r:<role id>``; ordered steps for a flow
   * - ``require_all``
     - ``False``
     - Multiple Approvers only: every approver must approve
   * - ``required_approvals``
     - ``None``
     - Multiple Approvers only: how many must approve (N of M)
   * - ``is_enabled``
     - ``True``
     - A disabled rule holds nothing
   * - ``risk_level``
     - ``"medium"``
     - ``"low"``, ``"medium"`` or ``"high"``; copied onto requests for filtering
   * - ``sla_enabled``
     - ``False``
     - Give each step a time limit
   * - ``sla_value``
     - ``None``
     - The limit, at least 1
   * - ``sla_unit``
     - ``None``
     - ``"minutes"``, ``"hours"`` or ``"days"``
   * - ``escalation``
     - ``""``
     - Token of the backup approver; required when the SLA is on
   * - ``conditions``
     - ``None``
     - Free-form data for handlers' ``matches()``, e.g. ``{"amount_gt": 10000}``

Validation
----------

A spec is refused, with every problem reported at once, when:

- the workflow or risk level is unknown;
- a Single Approver rule doesn't have exactly one approver, or a Multiple Approvers rule has fewer than two;
- ``required_approvals`` is set on another workflow, is outside 1 to the number of approvers, or is combined with
  ``require_all``;
- a step of an Approval Flow has no approver;
- the SLA is on without a limit of at least 1, a unit, or an escalation backup.

Approvers are then resolved against the tenant: each must be an active user or role of the tenant that holds the
action's ``approve_<action_type>`` permission.

A rule only holds actions when it is enabled and has at least one approver; a rule without approvers is ignored, so
an unfinished rule can never block an action.

N of M approvers
----------------

A Multiple Approvers rule can require any N of its approvers:

.. code-block:: python

   policies.PolicySpec(
       workflow_type=WorkflowType.MULTI,
       approvers=[f"u:{cfo.pk}", f"u:{treasurer.pk}", f"u:{controller.pk}"],
       required_approvals=2,                                     # any two of the three
   )

Each approver is one slot that one person fills, so listing a role more than once asks for that many *different*
members of it:

.. code-block:: python

   policies.PolicySpec(
       workflow_type=WorkflowType.MULTI,
       approvers=[f"r:{finance.pk}", f"r:{finance.pk}"],
       require_all=True,                                         # two people from Finance
   )

A request can only be raised when enough people other than the maker could approve each step; otherwise the
submission is refused with the reason, so a request never waits for approvals that can't happen.

Action-specific settings
------------------------

A rule's ``conditions`` hold settings only its action understands, such as amount thresholds per currency or the
accounts a rule covers. The action defines three hooks for them:

- ``matches(policy, payload)`` decides whether the rule holds a given change;
- ``validate_conditions(conditions)`` returns problems with the settings, which ``save_policy`` reports with the
  rule's other errors, so every screen and API enforces them;
- ``describe_conditions(conditions)`` returns the settings as ``{label: text}`` for rule history, so a change reads
  "Threshold: KES 10,000.00 → KES 25,000.00" rather than raw data.

.. code-block:: python

   def threshold_errors(conditions):
       amounts = [t.get("amount") for t in conditions.get("thresholds", [])]
       return [] if all(a and Decimal(a) > 0 for a in amounts) else ["Threshold amounts must be positive."]


   @approval_action(
       "payout",
       ...,
       matches=lambda policy, p: Decimal(p["amount"]) > threshold_for(policy.conditions, p["currency"]),
       validate_conditions=threshold_errors,
       describe_conditions=lambda c: {"Thresholds": ", ".join(f"{t['currency']} {t['amount']}" for t in c.get("thresholds", [])) or "None"},
   )

Rule history stores these descriptions, so keep their labels stable.

Scoped rules
------------

Actions that declare ``Scope.SCOPED`` can have rules per scope, a key your handler defines, such as
``"department:12"``. :func:`~django_maker_checker.policies.add_scoped_policy` creates an unconfigured rule for a
scope; a scoped rule wins over the tenant-wide rule for changes in that scope.

Rule changes never affect requests already raised: each request keeps a snapshot of the rule it was raised under.

History
-------

Every change made through :mod:`~django_maker_checker.policies` is recorded (see :ref:`concepts:Rule history`).
:func:`~django_maker_checker.queries.policy_history` lists a rule's revisions, newest first, for a history screen.
Deleting a rule with :func:`~django_maker_checker.policies.delete_policy` keeps its history.
