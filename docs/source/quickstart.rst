Quick start
===========

This walk-through puts a credit-limit change behind maker-checker in a single-tenant project on Django's own users,
groups and permissions, which needs no configuration beyond :doc:`installation`. For your own tenants, roles or
permission system, see :doc:`integration`.

1. Guard the action
-------------------

Decorate the function that makes the change. Its arguments become the request's payload, so they must be
JSON-serialisable:

.. code-block:: python

   # accounts/approvals.py
   from django_maker_checker.actions import approval_action


   @approval_action(
       "credit_limit_change",
       label="Credit Limit Change",
       category="Accounts",
       lock_key=lambda p: f"account:{p['account_id']}",  # at most one open request per account
       target_label=lambda p: f"Account {p['account_id']}",
   )
   def change_credit_limit(*, tenant, maker, context, account_id, new_limit):
       Account.objects.filter(pk=account_id).update(credit_limit=new_limit)
       return {"account_id": account_id}

Register it at start-up by importing the module from your app's ``AppConfig.ready()``:

.. code-block:: python

   class AccountsConfig(AppConfig):
       name = "accounts"

       def ready(self):
           from accounts import approvals  # noqa: F401  (registers the action)

The next ``migrate`` creates its permission, ``approvals.approve_credit_limit_change``.

2. Call it
----------

Call the function as before, adding the maker and a justification:

.. code-block:: python

   from django_maker_checker.models import Tenant

   outcome = change_credit_limit(
       tenant=Tenant.default(),
       maker=request.user,
       request=request,
       justification="Annual review",
       account_id=42,
       new_limit="5000.00",
   )
   if outcome.pending:
       messages.info(request, f"Sent for approval as {outcome.approval_request.reference}.")

With no rule configured the function simply runs, so adding maker-checker changes nothing until a rule is switched
on.

3. Choose the approvers
-----------------------

Approvers are users, or groups whose members may approve. Give a group the action's permission:

.. code-block:: python

   from django.contrib.auth.models import Group, Permission

   risk = Group.objects.create(name="Risk officers")
   risk.permissions.add(Permission.objects.get(codename="approve_credit_limit_change"))
   risk_officer.groups.add(risk)

4. Switch on a rule
-------------------

.. code-block:: python

   from django_maker_checker import policies
   from django_maker_checker.constants import WorkflowType

   rule = policies.policy_for(Tenant.default(), "credit_limit_change")
   policies.save_policy(
       rule,
       policies.PolicySpec(workflow_type=WorkflowType.SINGLE, approvers=[f"r:{risk.pk}"]),
       by=admin_user,
       reason="New credit policy",
   )

Approvers are tokens: ``u:<user id>`` for a user, ``r:<role id>`` for every member of a role (here, a group). From
now on, calling ``change_credit_limit`` raises an approval request instead of changing the limit, and the risk
officers are emailed.

5. Decide
---------

.. code-block:: python

   from django_maker_checker import services
   from django_maker_checker.constants import Decision

   services.decide(approval_request.pk, risk_officer, Decision.APPROVE, "Within appetite")

The credit limit changes now, recorded as the maker's change. A checker can also reject (with a reason) or send the
request back to the maker for changes:

.. code-block:: python

   services.decide(approval_request.pk, risk_officer, Decision.REJECT, "Exceeds appetite")
   services.decide(approval_request.pk, risk_officer, Decision.REQUEST_CHANGES, "Attach the latest statements")

Next steps
----------

- :doc:`concepts` explains the life cycle, who may decide, SLAs and the audit trail.
- :doc:`defining_actions` covers record edits and fully custom actions.
- :doc:`rules` covers workflows such as "2 of 3 approvers" and approval flows.
- :doc:`building_a_ui` lists the queries for building approval screens.
