"""The contract between the approvals engine and the host application.

The engine never imports the host. Everything it needs to know about tenancy, roles, permissions, scopes,
notifications and audit comes through one ``ApprovalsAdapter`` instance, named by the ``APPROVALS_ADAPTER``
setting. Subclass it in the host and implement every abstract method.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from django.utils.translation import gettext as _


@dataclass(frozen=True)
class Notice:
    """One notification event, delivered by the host to ``recipients`` over its own channels."""

    kind: str  # requested | decided | escalated | cancelled | sla_warning
    title: str
    text: str
    severity: str  # info | warning | error
    recipients: tuple
    approval_request: Any
    link: str = ""


@dataclass(frozen=True)
class AuditRecord:
    """One audit-trail entry for the host's central audit log."""

    action: str  # created | updated
    resource_type: str  # ApprovalRequest | ApprovalDelegation
    resource_id: str
    tenant_id: Any
    actor_id: Any = None
    before: Optional[dict] = None
    after: Optional[dict] = None
    metadata: dict = field(default_factory=dict)


class ApprovalsAdapter(ABC):
    """Host integration.

    ``tenant`` arguments are instances of ``APPROVALS_TENANT_MODEL`` and ``role`` arguments of ``APPROVALS_ROLE_MODEL``.
    """

    # --- tenancy and roles ----------------------------------------------------------------------

    @abstractmethod
    def tenant_of(self, user) -> Optional[Any]:
        """The tenant ``user`` acts for, or None if they are not an active member of one."""

    @abstractmethod
    def members(self, tenant, user_ids: Optional[Iterable] = None, role_ids: Optional[Iterable] = None):
        """Active users of ``tenant``. When ids are given, only users with one of ``user_ids`` or holding one of
        the (active) roles in ``role_ids``. Returns a queryset of ``AUTH_USER_MODEL``."""

    @abstractmethod
    def role_ids_of(self, user) -> set:
        """Ids of the active roles ``user`` holds."""

    @abstractmethod
    def roles(self, tenant):
        """Active roles of ``tenant`` (queryset of ``APPROVALS_ROLE_MODEL``)."""

    @abstractmethod
    def role_member_count(self, role) -> int:
        """Number of active members holding ``role`` (shown in approver pickers)."""

    # --- permissions ---------------------------------------------------------------------------

    @abstractmethod
    def has_permission(self, user, codename: str) -> bool:
        """Whether ``user`` holds the approvals permission ``codename`` (e.g. ``approve_user_create``)."""

    @abstractmethod
    def role_has_permission(self, role, codename: str) -> bool:
        """Whether members of ``role`` get the approvals permission ``codename`` through it."""

    # --- scopes --------------------------------------------------------------------------------

    def is_enabled(self, tenant) -> bool:
        """Whether maker-checker is switched on for ``tenant`` (e.g. a feature flag or a plan the host sells).

        While it is off the tenant is treated as having no rules: guarded actions run immediately and the SLA job
        leaves its requests alone. Rules, history and requests are kept, so switching it back on resumes where it
        stopped. Requests still open when it is switched off stay open; cancel them first if they must not linger.
        """
        return True

    def scope_label(self, tenant, scope_key: str) -> str:
        """Human name of a scope key returned by a handler's ``scope_keys`` (e.g. a department or account group)."""
        return scope_key

    # --- side effects --------------------------------------------------------------------------

    @abstractmethod
    def notify(self, notice: Notice) -> None:
        """Deliver ``notice`` (in-app, email, chat…). Called after the transaction commits."""

    @abstractmethod
    def audit(self, record: AuditRecord) -> None:
        """Write ``record`` to the host's audit log."""

    def request_url(self, approval_request) -> str:
        """Absolute or site-relative link to the request in the host UI (used in notifications)."""
        return ""

    # --- presentation helpers ------------------------------------------------------------------

    def user_label(self, user) -> str:
        """How a user is named in timelines, pickers and history; ``None`` means an automated (system) actor."""
        return (user.get_full_name() or user.get_username()) if user else _("System")

    def role_label(self, role) -> str:
        return str(role)
