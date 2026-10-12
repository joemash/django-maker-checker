Settings
========

Every setting is optional. With none of them, the engine runs single-tenant on Django's own users, groups and
permissions (see :ref:`integration:The built-in adapter`).

.. list-table::
   :header-rows: 1
   :widths: 30 20 50

   * - Setting
     - Default
     - Meaning
   * - ``APPROVALS_TENANT_MODEL``
     - ``"approvals.Tenant"``
     - ``"app_label.Model"`` of the tenant that owns rules and requests. The default is a built-in, single-row
       tenant.
   * - ``APPROVALS_ROLE_MODEL``
     - ``"auth.Group"``
     - ``"app_label.Model"`` of a role that can be named as an approver.
   * - ``APPROVALS_ADAPTER``
     - the built-in adapter
     - Dotted path of your :class:`~django_maker_checker.adapter.ApprovalsAdapter` subclass. Required when
       ``APPROVALS_TENANT_MODEL`` is your own model.
   * - ``APPROVALS_CACHE_ALIAS``
     - ``"default"``
     - Cache used for pending counts.
   * - ``APPROVALS_CACHE_TIMEOUT``
     - ``60``
     - Seconds a user's pending count is cached.
   * - ``APPROVALS_REFERENCE_PREFIX``
     - ``"REQ"``
     - Prefix of request references, as in ``REQ-2610-00042``.

The tenant and role models are referenced by the engine's migrations, like ``AUTH_USER_MODEL``: set them before the
first ``migrate`` and don't change them afterwards.
