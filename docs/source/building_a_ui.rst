Building a UI
=============

The library ships no views or templates. :mod:`~django_maker_checker.queries` returns everything an approvals screen
needs, already filtered by what the viewer may see, and :mod:`~django_maker_checker.services` performs the actions.

Queues
------

.. code-block:: python

   from django_maker_checker import queries

   waiting = queries.awaiting(request.user)                         # waiting for this user's decision now
   mine = queries.made_by(request.user, open_only=True)             # the user's own open requests
   history = queries.checker_history(request.user)                  # decided requests the user may review
   badge = queries.pending_count(request.user)                      # cached per tenant, for a menu badge

Each takes an optional :class:`~django_maker_checker.queries.RequestFilter`:

.. code-block:: python

   filters = queries.RequestFilter(
       status="pending", action_type="payment_run", risk_level="high", maker_id=maker.pk,
       submitted_from=date(2026, 10, 1), submitted_to=date(2026, 10, 31), text="REQ-2610",
   )
   rows = queries.awaiting(request.user, filters)

``text`` matches the reference, the item's name, the action's label and the maker's name, email or username, ignoring
case. ``queries.makers(rows)`` lists the makers for a filter drop-down.

A request's page
----------------

=====================================================  ====================================================================
Query                                                  Gives
=====================================================  ====================================================================
``allowed_actions(user, request)``                     Which buttons to show: decide, cancel, retry
``sla_status(request)``                                The current step's SLA: state, percent used, time left, who it
                                                       escalated to
``stage_progress(request)``                            Every step: its approvers, approvals done of required, and state
``current_approvers_label(request)``                   Who the request is waiting for, as text
``policy_version_status(request)``                     Whether the rule has changed since the request was raised
``request.events.all()``                               The timeline
=====================================================  ====================================================================

The handler supplies the review panel: render its ``review_template`` with ``handler.review_context(request)``. The
default context is a list of Current/Proposed rows built from the snapshot and the payload.

Opening a request should call ``services.record_view(request, user)``, which logs the first view by each current
approver on the timeline.

Actions
-------

==================================================================  ===========================================
Call                                                                Does
==================================================================  ===========================================
``services.decide(request_id, user, Decision.APPROVE, comment)``    Approve; the last approval applies the change
``services.decide(request_id, user, Decision.REJECT, reason)``      Reject; a reason is required
``services.cancel(request_id, user, reason)``                       Withdraw (the maker, or an administrator)
``services.retry(request_id, user)``                                Re-apply a failed request (administrators)
``services.add_comment(request_id, user, text)``                    Add a note to the timeline
``services.delegate(...)`` / ``services.revoke_delegation(...)``    Hand approvals to a colleague for a period
==================================================================  ===========================================

Each raises :class:`~django_maker_checker.exceptions.ApprovalError` with a message you can show when the user may
not perform it, so views can stay thin.

Rule screens
------------

``policies.policy_for(tenant, action_type, scope_key)`` gives the rule to edit,
``permissions.approver_choices(tenant, action_type)`` the users and roles that may be picked as approvers,
``registry.all_handlers(scope)`` the actions to list, and ``queries.policy_history(...)`` a rule's history. Restrict
these screens with ``permissions.can_manage_policies(user)``.
