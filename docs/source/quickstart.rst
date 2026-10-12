Quick start
===========

This walk-through puts a credit-limit change behind maker-checker in a project where each user has a ``Membership``
in one ``Organisation`` with one ``Role``. It assumes the :doc:`installation` steps are done.

1. Tell the engine about your users
-----------------------------------

The engine never imports your code. Everything it needs to know about tenants, roles, permissions, notifications and
auditing goes through one adapter class (see :doc:`integration` for every method):

.. code-block:: python

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
           users = get_user_model().objects.filter(
               membership__organisation=tenant, membership__active=True, is_active=True
           )
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
           ...  # send notice.text to notice.recipients by email, in-app or chat

       def audit(self, record):
           ...  # write the AuditRecord to your audit log

2. Guard the action
-------------------

Decorate the function that makes the change. Its arguments become the request's payload, so they must be
JSON-serialisable:

.. code-block:: python

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

Register it at start-up by importing the module from your app's ``AppConfig.ready()``:

.. code-block:: python

   class ApprovalsIntegrationConfig(AppConfig):
       name = "approvals_integration"

       def ready(self):
           from approvals_integration import actions  # noqa: F401  (registers the actions)

3. Call it
----------

Call the function as before, adding the maker and a justification:

.. code-block:: python

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

With no rule configured for the tenant the function simply runs, so adding maker-checker changes nothing until a
tenant switches it on.

4. Switch on a rule
-------------------

.. code-block:: python

   from django_maker_checker import policies
   from django_maker_checker.constants import WorkflowType

   rule = policies.policy_for(organisation, "credit_limit_change")
   policies.save_policy(
       rule,
       policies.PolicySpec(workflow_type=WorkflowType.SINGLE, approvers=[f"r:{risk_officer_role.pk}"]),
       by=admin_user,
       reason="New credit policy",
   )

Approvers are tokens: ``u:<user id>`` for a user, ``r:<role id>`` for every member of a role. They must hold the
action's permission, here ``approve_credit_limit_change``; the engine names it and your project grants it (see
:ref:`integration:Permissions`).

From now on, calling ``change_credit_limit`` raises an approval request instead of changing the limit.

5. Approve
----------

.. code-block:: python

   from django_maker_checker import services
   from django_maker_checker.constants import Decision

   services.decide(approval_request.pk, risk_officer, Decision.APPROVE, "Within appetite")

The credit limit changes now, recorded as the maker's change. Rejecting needs a reason:

.. code-block:: python

   services.decide(approval_request.pk, risk_officer, Decision.REJECT, "Exceeds appetite")

Next steps
----------

- :doc:`concepts` explains the life cycle, who may decide, SLAs and the audit trail.
- :doc:`defining_actions` covers record edits and fully custom actions.
- :doc:`building_a_ui` lists the queries for building approval screens.
