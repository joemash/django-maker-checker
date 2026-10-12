Integrating with your project
=============================

The engine knows nothing about your users, organisations or permission system. It asks one object, your
:class:`~django_maker_checker.adapter.ApprovalsAdapter` subclass, named by the ``APPROVALS_ADAPTER`` setting.

The adapter
-----------

Required methods:

============================================================  ==============================================================
Method                                                        Answers
============================================================  ==============================================================
``tenant_of(user)``                                           The tenant the user acts for, or ``None`` if they are not an
                                                              active member.
``members(tenant, user_ids=None, role_ids=None)``             Active users of the tenant, optionally only those with the
                                                              given ids or roles.
``role_ids_of(user)``                                         Ids of the user's active roles.
``roles(tenant)``                                             The roles that can be picked as approvers.
``role_member_count(role)``                                   How many active users hold the role.
``has_permission(user, codename)``                            Your permission check for the user.
``role_has_permission(role, codename)``                       Whether the role grants the permission.
``notify(notice)``                                            Deliver a :class:`~django_maker_checker.adapter.Notice`.
                                                              Called after commit; a failure is logged and never undoes a
                                                              decision.
``audit(record)``                                             Write an :class:`~django_maker_checker.adapter.AuditRecord` to
                                                              your audit log.
============================================================  ==============================================================

Optional methods:

============================================================  ==============================================================
Method                                                        Default
============================================================  ==============================================================
``is_enabled(tenant)``                                        ``True``. See `Switching maker-checker on per tenant`_.
``scope_label(tenant, scope_key)``                            The scope key itself.
``request_url(approval_request)``                             ``""``. A link to the request, used in notifications.
``user_label(user)``                                          The user's full name, or username.
``role_label(role)``                                          ``str(role)``.
============================================================  ==============================================================

The :doc:`quickstart` has a complete adapter for a project with ``Membership`` and ``Role`` models.

Notifications
-------------

The engine decides *who* hears about an event; your ``notify()`` delivers it over your channels.

=============  ==================================================================================
Notice kind    Sent to
=============  ==================================================================================
requested      The current approvers and their delegates: a request is waiting for them
decided        The maker: applied, rejected (with the reason, severity ``warning``) or approved but failed
               to apply (severity ``error``)
cancelled      The current approvers, and the maker when an administrator cancelled
sla_warning    The current approvers: the deadline is near
escalated      The backup approver, or the current approvers when there is none
=============  ==================================================================================

Permissions
-----------

The engine only names the permission codenames it checks:

- ``approve_<action_type>`` for each action (:attr:`ActionHandler.approve_permission
  <django_maker_checker.registry.ActionHandler.approve_permission>`), needed to be an approver;
- ``manage_approval_policies`` (:data:`django_maker_checker.permissions.MANAGE_POLICIES`), for administrators who
  configure rules, cancel anyone's request and retry failed ones.

Your project creates and grants them. A ``post_migrate`` handler that loops over
:func:`~django_maker_checker.registry.all_handlers` keeps them in step with the registered actions:

.. code-block:: python

   from django.contrib.auth.models import Permission
   from django.contrib.contenttypes.models import ContentType

   from django_maker_checker.registry import all_handlers


   def sync_approval_permissions(sender, **kwargs):
       content_type = ContentType.objects.get_for_model(Organisation)
       for handler in all_handlers():
           Permission.objects.get_or_create(
               codename=handler.approve_permission,
               content_type=content_type,
               defaults={"name": handler.approve_permission_label},
           )

Switching maker-checker on per tenant
-------------------------------------

Override ``is_enabled(tenant)`` to put the feature behind a feature flag, a plan or a setting:

.. code-block:: python

   class Approvals(ApprovalsAdapter):
       def is_enabled(self, tenant):
           return tenant.plan.includes_approvals

While it returns ``False`` the tenant is treated as having no rules: guarded actions run immediately,
``requires_approval`` is ``False``, ``submit`` refuses, and the SLA job skips the tenant. Rules, history and requests
are kept, so switching back on resumes where it stopped. Requests that are open at the switch stay open and can still
be cancelled; hide your approval screens and cancel them if they must not linger.

Migration order
---------------

Django resolves ``APPROVALS_TENANT_MODEL`` and ``APPROVALS_ROLE_MODEL`` to their app's *first* migration. If those
models are created in a later migration, add an empty migration to your integration app that depends on it and runs
before the engine:

.. code-block:: python

   class Migration(migrations.Migration):
       dependencies = [("accounts", "0007_role")]
       run_before = [("approvals", "0001_initial")]
       operations = []

As with ``AUTH_USER_MODEL``, choose the tenant and role models before the first migration; changing them later is not
supported.

Reacting to events
------------------

========================================  ===================================================================
Need                                      Hook
========================================  ===================================================================
A request's outcome                       ``django_fsm.signals.post_transition`` with ``sender=ApprovalRequest``.
                                          It fires inside the transaction, before the save; use
                                          ``transaction.on_commit`` for side effects.
Anything on the timeline                  ``post_save`` on ``ApprovalEvent`` (``created=True``), by ``type``.
Doing something when your action is       Your handler's ``execute()``.
approved
========================================  ===================================================================
