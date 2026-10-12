"""Hand audit records to the host without letting an audit-sink failure break the operation being audited."""

import logging

from django_maker_checker.conf import get_adapter

logger = logging.getLogger(__name__)


def send(record):
    """Pass ``record`` to ``adapter.audit``. Failures are logged and swallowed: the engine's own records (request
    events, policy revisions) are written in the same transaction and stay authoritative, so an unavailable audit
    sink must not roll back a decision or a rule change."""
    try:
        get_adapter().audit(record)
    except Exception:  # noqa: BLE001 - the engine's own records (events, revisions) are authoritative
        logger.exception("Audit sink failed for %s %s", record.resource_type, record.resource_id)
