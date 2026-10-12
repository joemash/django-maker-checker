API reference
=============

Services
--------

.. automodule:: django_maker_checker.services
   :members: submit_or_execute, submit, find_policy, requires_approval, decide, resubmit, execute_approved, retry, cancel, add_comment, record_view, delegate, revoke_delegation, process_sla

Rules
-----

.. automodule:: django_maker_checker.policies
   :members: PolicySpec, PolicyError, policy_for, save_policy, add_scoped_policy, delete_policy, describe, current_version

Queries
-------

.. automodule:: django_maker_checker.queries
   :members: RequestFilter, awaiting, made_by, checker_history, makers, open_count, pending_count, allowed_actions, AllowedActions, sla_status, SlaStatus, stage_progress, StageProgress, current_approvers_label, approvable_action_types, policy_history, policy_version_status, PolicyVersionStatus

Permissions
-----------

.. automodule:: django_maker_checker.permissions
   :members:

Defining actions
----------------

.. automodule:: django_maker_checker.registry
   :members: ActionHandler, ExecutionContext, Scope, register, register_handler, get_handler, all_handlers, diff_rows

.. automodule:: django_maker_checker.actions
   :members: approval_action, Outcome, to_payload

.. automodule:: django_maker_checker.model_change
   :members: ModelChangeHandler

Adapter
-------

.. automodule:: django_maker_checker.adapter
   :members: ApprovalsAdapter, Notice, AuditRecord

.. automodule:: django_maker_checker.auth_adapter
   :members: AuthApprovalsAdapter, sync_permissions

Exceptions
----------

.. automodule:: django_maker_checker.exceptions
   :members:

Constants
---------

.. automodule:: django_maker_checker.constants
   :members:
   :undoc-members:

Models
------

Read the models freely, but change state only through :mod:`~django_maker_checker.services` and
:mod:`~django_maker_checker.policies`, which enforce the rules and record the audit trail.

.. automodule:: django_maker_checker.models
   :members: Tenant, ApprovalPolicy, ApprovalPolicyApprover, ApprovalPolicyRevision, ApprovalRequest, ApprovalStage, ApprovalStageApprover, ApprovalDecision, ApprovalEvent, ApprovalDelegation
   :exclude-members: DoesNotExist, MultipleObjectsReturned
