Defining actions
================

Whether an action needs approval is data, decided per tenant by its rules. Code only describes how the action is
captured, shown and applied. Each action is one handler registered under an ``action_type``; pick the lightest of
three ways to write it:

===================================================  =====================================  =============================
The action is…                                       Use                                    You write
===================================================  =====================================  =============================
A service function you can call with plain values    ``@approval_action``                   A decorator
A change to some fields of one model record          ``ModelChangeHandler``                 A subclass naming the fields
Anything else: files, many records, external calls   ``ActionHandler``                      A full handler class
===================================================  =====================================  =============================

In every case:

- **One code path.** With no applicable rule the action runs immediately, so the call site is the same whether or not
  a tenant has switched approval on.
- **Payloads are JSON.** They are stored on the request and must survive a JSON round trip; ``Decimal`` and dates
  arrive as strings. The same conversion happens when no approval is needed, so behaviour never depends on the rule.
- **Register at start-up.** Import your handler modules from your app's ``AppConfig.ready()``. An action type can be
  defined in only one place.

``@approval_action``: defer a function call
-------------------------------------------

The call is captured as the payload when a rule applies, and replayed as the maker's change once approved.

.. code-block:: python

   from django_maker_checker.actions import approval_action
   from django_maker_checker.registry import Scope


   @approval_action(
       "balance_update",
       label="Balance Update",
       category="Balances",
       lock_key=lambda p: f"{p['account_id']}:{p['balance_date']}",
       target_label=lambda p: f"Account {p['account_id']} on {p['balance_date']}",
       snapshot_before=lambda tenant, p: {"new_balance": current_balance(tenant, p)},
       scopes=(Scope.GLOBAL, Scope.SCOPED),
       scope_keys=lambda tenant, p: [f"department:{department_of(p['account_id'])}"],
       matches=lambda policy, p: amount_above(policy.conditions.get("amount_gt"), p),
   )
   def update_balance(*, tenant, maker, context, account_id, balance_date, new_balance):
       ...  # the change itself; context.actor is the final checker
       return {"account_id": account_id}

The function must take keyword-only arguments. ``tenant``, ``maker`` and ``context`` are supplied by the engine;
everything else is the payload. Options:

=====================  =============================================================================================
Option                 Meaning
=====================  =============================================================================================
``lock_key``           Required. Identifies the item, so only one open request exists per item.
``target_label``       The item's name in queues and notifications.
``snapshot_before``    The item's current state, shown as "Current" when reviewing and kept on the request.
``scopes``             Where rules may be configured: ``Scope.GLOBAL`` (tenant-wide) and/or ``Scope.SCOPED``.
``scope_keys``         The scopes a change belongs to, most specific first; a scoped rule for any of them wins.
``matches``            Whether a rule's ``conditions`` select this change, e.g. an amount threshold.
``validate``           Re-check, right before applying, that the change can still be made.
=====================  =============================================================================================

Calling the decorated function returns an :class:`~django_maker_checker.actions.Outcome`: ``pending`` with the
``approval_request``, or the function's ``result``. ``update_balance.apply(...)`` calls the undecorated function, for
tests and admin tools that must bypass approval.

``ModelChangeHandler``: defer a record edit
-------------------------------------------

.. code-block:: python

   from django_maker_checker.model_change import ModelChangeHandler
   from django_maker_checker.registry import register


   @register
   class CreditLimitChangeHandler(ModelChangeHandler):
       action_type = "credit_limit_change"
       label = "Credit Limit Change"
       category = "Accounts"
       model = Account
       fields = ("credit_limit",)      # nothing else can change through this action
       tenant_field = "organisation"   # the model's foreign key to the tenant (default "tenant")


   outcome = CreditLimitChangeHandler.submit_change(
       account, {"credit_limit": "5000.00"}, maker=request.user, justification="Annual review"
   )

The base class takes care of:

- one open request per record;
- a snapshot of the fields being changed;
- an optimistic check when approved: if the record changed since submission, the request fails instead of
  overwriting someone else's edit;
- converting the stored values back to field types, validating and saving;
- Current/Proposed rows for the review panel, labelled with the fields' verbose names.

Records are always looked up through the tenant, so a payload can't reach another tenant's record. Override
``queryset(tenant)``, ``describe(obj)`` or ``apply(obj, changes, context)`` to change how records are found, named or
saved.

``ActionHandler``: full control
-------------------------------

.. code-block:: python

   from django_maker_checker.registry import ActionHandler, Scope, register


   @register
   class PaymentRunHandler(ActionHandler):
       action_type = "payment_run"
       label = "Payment Run"
       category = "Payments"
       scopes = (Scope.GLOBAL,)
       review_template = "payments/review/payment_run.html"

       def lock_key(self, payload): ...                    # required
       def target_label(self, payload): ...
       def snapshot_before(self, tenant, payload): ...
       def scope_keys(self, tenant, payload): ...
       def matches(self, policy, payload): ...
       def validate(self, approval_request): ...           # raise ApprovalError if it can no longer be applied
       def execute(self, approval_request, context): ...   # apply; return a JSON-safe result
       def review_context(self, approval_request): ...     # data for review_template

Submit it with :func:`~django_maker_checker.services.submit_or_execute`:

.. code-block:: python

   approval_request, result = services.submit_or_execute(
       "payment_run", tenant=tenant, maker=request.user, payload={"run_id": run.pk}, justification="Month end",
       request=request,
   )

Rules for ``execute()``:

- It runs in a transaction. Raise to roll back; the request is recorded as ``FAILED`` and can be retried.
- Queue background work with ``transaction.on_commit``.
- Make writes to external systems safe to retry.
- It is also called directly, with a stand-in request, when no rule applies, so it must not assume an approval took
  place.

Guarding a model method used in many places
-------------------------------------------

When the action is a model method called from several views:

1. Run the existing business rules first.
2. If :func:`~django_maker_checker.services.find_policy` returns a rule, ``raise ApprovalPending(services.submit(...))``.
3. Re-enter the method from the handler's ``execute()`` with a flag that skips step 2.

:class:`~django_maker_checker.exceptions.ApprovalPending` subclasses ``ValueError``, so a caller you missed still shows
a sensible message.
