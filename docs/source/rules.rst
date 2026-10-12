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

==================  ====================  ==========================================================================
Field               Default               Meaning
==================  ====================  ==========================================================================
``workflow_type``   ``"single"``          ``"single"``, ``"multi"`` or ``"sequential"`` (Approval Flow)
``approvers``       ``[]``                Tokens: ``u:<user id>`` or ``r:<role id>``; ordered steps for a flow
``require_all``     ``False``             Multiple Approvers only: every approver must approve
``is_enabled``      ``True``              A disabled rule holds nothing
``risk_level``      ``"medium"``          ``"low"``, ``"medium"`` or ``"high"``; copied onto requests for filtering
``sla_enabled``     ``False``             Give each step a time limit
``sla_value``       ``None``              The limit, at least 1
``sla_unit``        ``None``              ``"minutes"``, ``"hours"`` or ``"days"``
``escalation``      ``""``                Token of the backup approver; required when the SLA is on
``conditions``      ``None``              Free-form data for handlers' ``matches()``, e.g. ``{"amount_gt": 10000}``
==================  ====================  ==========================================================================

Validation
----------

A spec is refused, with every problem reported at once, when:

- the workflow or risk level is unknown;
- a Single Approver rule doesn't have exactly one approver, or a Multiple Approvers rule has fewer than two;
- a step of an Approval Flow has no approver;
- the SLA is on without a limit of at least 1, a unit, or an escalation backup.

Approvers are then resolved against the tenant: each must be an active user or role of the tenant that holds the
action's ``approve_<action_type>`` permission.

A rule only holds actions when it is enabled and has at least one approver; a rule without approvers is ignored, so
an unfinished rule can never block an action.

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
