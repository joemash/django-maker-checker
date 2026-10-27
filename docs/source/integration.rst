Integrating with your project
=============================

The engine knows nothing about your users, organisations or permission system. It asks one object, an
:class:`~django_maker_checker.adapter.ApprovalsAdapter`, named by the ``APPROVALS_ADAPTER`` setting. You either use
the built-in adapter, or write your own.

The built-in adapter
--------------------

With ``APPROVALS_TENANT_MODEL``, ``APPROVALS_ROLE_MODEL`` and ``APPROVALS_ADAPTER`` unset, the engine uses
:class:`~django_maker_checker.auth_adapter.AuthApprovalsAdapter`, for single-tenant projects on Django's ``auth``
app:

- **Tenant:** one built-in tenant, ``Tenant.default()``, which every active user belongs to.
- **Roles:** Django groups. A rule can name a group, and any of its members may approve.
- **Permissions:** ordinary Django permissions in the ``approvals`` app, created after every ``migrate``:
  ``approve_<action_type>`` for each registered action and ``manage_approval_policies`` for administrators. Grant
  them to users or groups as usual; superusers have them all, but still can't approve their own changes.
- **Notifications:** emailed with ``django.core.mail`` to recipients with an email address. Set
  ``DEFAULT_FROM_EMAIL``, and override ``request_url()`` to include a link to your approval screen.
- **Audit:** each :class:`~django_maker_checker.adapter.AuditRecord` is logged on the ``django_maker_checker.audit``
  logger, with the record in the log entry's ``audit`` attribute.

Subclass it to change any of these, and point ``APPROVALS_ADAPTER`` at your subclass:

.. code-block:: python

   from django_maker_checker.auth_adapter import AuthApprovalsAdapter


   class Approvals(AuthApprovalsAdapter):
       def request_url(self, approval_request):
           return f"https://app.example.com/approvals/{approval_request.reference}/"

       def audit(self, record):
           AuditLog.objects.create(action=record.action, resource=record.resource_type, data=record.after or {})

Your own adapter
----------------

For multi-tenant projects, or your own roles or permission system, set ``APPROVALS_TENANT_MODEL``,
``APPROVALS_ROLE_MODEL`` and ``APPROVALS_ADAPTER``, and subclass ``ApprovalsAdapter``. For a project where each user
has a ``Membership`` in one ``Organisation`` with one ``Role``:

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

Adapter methods
---------------

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

Notifications
-------------

The engine decides *who* hears about an event; your ``notify()`` delivers it over your channels.

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Notice kind
     - Sent to
   * - ``requested``
     - The current approvers and their delegates: a request is waiting for them
   * - ``decided``
     - The maker: applied, rejected (with the reason, severity ``warning``), or approved but failed to apply (severity ``error``)
   * - ``changes_requested``
     - The maker: the request was sent back, with what to change
   * - ``cancelled``
     - The current approvers, and the maker when an administrator cancelled
   * - ``sla_warning``
     - The current approvers: the deadline is near
   * - ``escalated``
     - The backup approver, or the current approvers when there is none

Permissions
-----------

The engine only names the permission codenames it checks:

- ``approve_<action_type>`` for each action (:attr:`ActionHandler.approve_permission
  <django_maker_checker.registry.ActionHandler.approve_permission>`), needed to be an approver;
- ``manage_approval_policies`` (:data:`django_maker_checker.permissions.MANAGE_POLICIES`), for administrators who
  configure rules, cancel anyone's request and retry failed ones.

The built-in adapter creates them for you. With your own adapter, your project creates and grants them. A
``post_migrate`` handler that loops over
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

When an approver leaves
-----------------------

Requests keep the approvers they were raised with, so a step that names someone who has been deactivated can't be
approved. Call ``services.reassign_approver(user, by=admin)`` from your deactivate or remove-user flow, after the user
has lost access. Each of their unfilled slots goes to the approver the rule names in the same position now, or else to
the request's escalation backup; a slot nobody can take over stays as it is and the request's timeline says so.

Read replicas
-------------

A database router may send reads to a replica. Decisions read what they have just written inside one transaction, so
the engine reads a request's stages, slots and decisions from the database the request was loaded from (the write
database, for a request locked for a decision) instead of asking the router. Your own handlers' ``validate()`` and
``execute()`` run inside that transaction too: if they read data that the change, or a request raised moments ago,
has just written, read it from the write database (``Model.objects.db_manager(router.db_for_write(Model))``).
