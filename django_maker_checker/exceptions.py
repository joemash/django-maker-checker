"""Exceptions raised to callers. Messages are written for end users, since hosts usually show them as-is."""

from django.utils.translation import gettext as _


class ApprovalError(Exception):
    """A maker-checker operation was refused; the message is safe to show to the user."""


class ApprovalPending(ValueError):
    """Raised from inside a guarded action when a maker-checker request was created instead.

    Subclasses ValueError so callers that already surface ValueError messages degrade gracefully;
    known callers catch it explicitly and report it as a success ("submitted for approval").
    """

    def __init__(self, approval_request):
        # Keep the request so callers can show its reference or link to it.
        self.approval_request = approval_request
        super().__init__(
            _("Submitted for approval as %(reference)s. The change will be applied once a checker approves it.")
            % {"reference": approval_request.reference}
        )
