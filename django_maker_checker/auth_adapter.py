"""The built-in adapter, for single-tenant projects that use Django's own users, groups and permissions.

It is used when ``APPROVALS_TENANT_MODEL`` and ``APPROVALS_ADAPTER`` are not set:

- every active user belongs to the one built-in tenant (``Tenant.default()``);
- roles are Django groups (``auth.Group``);
- permissions are ordinary Django permissions in the ``approvals`` app (``approvals.approve_<action_type>``,
  ``approvals.manage_approval_policies``), created after every ``migrate`` by ``sync_permissions`` and granted to
  users or groups as usual;
- notifications are emailed with ``django.core.mail`` to recipients that have an email address;
- audit records are written to the ``django_maker_checker.audit`` logger.

Subclass it to change one of these, e.g. ``request_url`` for links in notification emails.
"""

import logging
from dataclasses import asdict

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.mail import send_mail
from django.db.models import Q

from django_maker_checker.adapter import ApprovalsAdapter
from django_maker_checker.models import ApprovalPolicy, Tenant
from django_maker_checker.permissions import MANAGE_POLICIES
from django_maker_checker.registry import all_handlers

audit_logger = logging.getLogger("django_maker_checker.audit")

APP_LABEL = "approvals"


class AuthApprovalsAdapter(ApprovalsAdapter):
    """Maker-checker on Django's ``auth`` app, with one tenant."""

    # --- tenancy and roles ----------------------------------------------------------------------

    def tenant_of(self, user):
        if not getattr(user, "is_authenticated", False) or not user.is_active:
            return None
        return Tenant.default()

    def members(self, tenant, user_ids=None, role_ids=None):
        users = get_user_model().objects.filter(is_active=True)
        if user_ids is not None or role_ids is not None:
            users = users.filter(Q(pk__in=list(user_ids or ())) | Q(groups__in=list(role_ids or ())))
        return users.distinct()

    def role_ids_of(self, user):
        return set(user.groups.values_list("pk", flat=True))

    def roles(self, tenant):
        return Group.objects.all()

    def role_member_count(self, role):
        return role.user_set.filter(is_active=True).count()

    # --- permissions ----------------------------------------------------------------------------

    def has_permission(self, user, codename):
        return user.has_perm(f"{APP_LABEL}.{codename}")

    def role_has_permission(self, role, codename):
        return role.permissions.filter(content_type__app_label=APP_LABEL, codename=codename).exists()

    # --- side effects ---------------------------------------------------------------------------

    def notify(self, notice):
        emails = [user.email for user in notice.recipients if getattr(user, "email", "")]
        if not emails:
            return
        body = f"{notice.text}\n\n{notice.link}" if notice.link else notice.text
        send_mail(notice.title, body, None, emails)

    def audit(self, record):
        audit_logger.info(
            "%s %s %s", record.action, record.resource_type, record.resource_id, extra={"audit": asdict(record)}
        )


def sync_permissions(using="default", **kwargs):
    """Create the ``approvals`` permissions the built-in adapter checks: one per registered action, plus the
    administrators' permission. Runs after every ``migrate``, so new actions get their permission automatically."""
    content_type = ContentType.objects.db_manager(using).get_for_model(ApprovalPolicy)
    # Permission names are stored in the database, so they stay in English like Django's own.
    wanted = {MANAGE_POLICIES: "Manage maker-checker approval rules"}
    wanted.update({handler.approve_permission: handler.approve_permission_label for handler in all_handlers()})
    for codename, name in wanted.items():
        Permission.objects.db_manager(using).get_or_create(
            content_type=content_type, codename=codename, defaults={"name": name[:255]}
        )
