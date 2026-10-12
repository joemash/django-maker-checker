Settings
========

==============================  =============  =========================================================================
Setting                         Default        Meaning
==============================  =============  =========================================================================
``APPROVALS_TENANT_MODEL``      required       ``"app_label.Model"`` of the tenant that owns rules and requests
``APPROVALS_ROLE_MODEL``        required       ``"app_label.Model"`` of a role that can be named as an approver
``APPROVALS_ADAPTER``           required       Dotted path of your
                                               :class:`~django_maker_checker.adapter.ApprovalsAdapter` subclass
``APPROVALS_CACHE_ALIAS``       ``"default"``  Cache used for pending counts
``APPROVALS_CACHE_TIMEOUT``     ``60``         Seconds a user's pending count is cached
``APPROVALS_REFERENCE_PREFIX``  ``"REQ"``      Prefix of request references, as in ``REQ-2610-00042``
==============================  =============  =========================================================================

The tenant and role models are referenced by the engine's migrations, like ``AUTH_USER_MODEL``: set them before the
first ``migrate`` and don't change them afterwards.
