Defining actions
================

Whether an action needs approval is data, decided per tenant by its rules. Code only describes how the action is
captured, shown and applied. Each action is one handler registered under an ``action_type``; pick the lightest of
three ways to write it:

==============================.. list-table::
   :header-rows: 1
   :widths: 28 72

   * - Option
     - Meaning
   * - ``lock_key``
     - Required. Identifies the item, so only one open request exists per item.
   * - ``target_label``
     - The item's name in queues and notifications.
   * - ``snapshot_before``
     - The item's current state, shown as "Current" when reviewing and kept on the request.
   * - ``scopes``
     - Where rules may be configured: ``Scope.GLOBAL`` (tenant-wide) and/or ``Scope.SCOPED``.
   * - ``scope_keys``
     - The scopes a change belongs to, most specific first; a scoped rule for any of them wins.
   * - ``matches``
     - Whether a rule's ``conditions`` select this change, e.g. an amount threshold.
   * - ``validate``
     - Re-check, right before applying, that the change can still be made.
   * - ``lock_keys``
     - Every item the change touches; see `Changes that touch several items`_.
   * - ``validate_conditions``
     - Check the rule's ``conditions`` for this action; see :ref:`rules:Action-specific settings`.
   * - ``describe_conditions``
     - Describe the rule's ``conditions`` for rule history.

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
       def lock_keys(self, payload): ...                   # every item touched, locked across actions
       def target_label(self, payload): ...
       def snapshot_before(self, tenant, payload): ...
       def scope_keys(self, tenant, payload): ...
       def matches(self, policy, payload): ...
       def validate_conditions(self, conditions): ...      # problems with the rule's settings
       def describe_conditions(self, conditions): ...      # the rule's settings, for history
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

Changes that touch several items
--------------------------------

``lock_key`` names the one item a change is about, and stops a second open request of the same action for it. When
a change touches several items, such as every transaction in a manual match, also return them from ``lock_keys``:

.. code-block:: python

   @approval_action(
       "manual_match",
       label="Manual Reconciliation",
       category="Reconciliation",
       lock_key=lambda p: f"txn:{p['transaction_id']}",                                # the transaction being matched
       lock_keys=lambda p: [f"txn:{t}" for t in [p["transaction_id"], *p["matched_ids"]]],  # and everything it is matched to
   )
   def manual_match(*, tenant, maker, context, transaction_id, matched_ids, comment=""):
       ...

Every key is locked while the request is open, across all action types, so no other open request (a second match,
or an unmatch of the same transaction) can claim any of them; the submission is refused with the reference of the
request holding them. Locks are released when the request is applied, rejected, cancelled or fails, and taken again
if it is retried or resubmitted.

:func:`~django_maker_checker.queries.open_requests_for` returns the request holding each item, for marking items
that are waiting for approval:

.. code-block:: python

   pending = queries.open_requests_for(tenant, [f"txn:{t.id}" for t in page_of_transactions])
   # {"txn:42": <ApprovalRequest REQ-2610-00042>, ...}

Guarding a model method used in many places
-------------------------------------------

When the action is a model method called from several views:

1. Run the existing business rules first.
2. If :func:`~django_maker_checker.services.find_policy` returns a rule, ``raise ApprovalPending(services.submit(...))``.
3. Re-enter the method from the handler's ``execute()`` with a flag that skips step 2.

:class:`~django_maker_checker.exceptions.ApprovalPending` subclasses ``ValueError``, so a caller you missed still shows
a sensible message.
