"""Translations: what the engine translates, and what it deliberately keeps in English.

Text shown to people goes through Django's translation machinery, so a project's active language (and its message
catalogs) decide how it reads: error messages raised as ``ApprovalError`` / ``PolicyError``, timeline messages,
notification titles and texts, SLA labels, and the labels of every choice (statuses, workflows, risk levels…).

Kept in English on purpose:

- a rule's revision history (``policies.describe``): it is stored and compared across revisions, so it must not
  depend on the language active when a rule was saved;
- permission names (``Approve …``), which are stored in the database like Django's own;
- codes (statuses, event and notice kinds), log messages and errors meant for developers.

The package ships an English catalog in ``django_maker_checker/locale``; add a language with
``django-admin makemessages -l <code>`` from inside the package.
"""

from unittest import mock

from django.utils import translation
from django.utils.functional import Promise

from django_maker_checker import policies, services
from django_maker_checker.constants import Decision, RequestStatus, WorkflowType
from django_maker_checker.exceptions import ApprovalError
from django_maker_checker.policies import PolicySpec
from tests.base import EngineTestCase
from tests.testapp.handlers import PUBLISH


class WhatIsTranslated(EngineTestCase):
    def test_choice_labels_are_translated_when_displayed(self):
        self.assertTrue(all(isinstance(label, Promise) for _code, label in RequestStatus.CHOICES))

    def test_messages_go_through_gettext(self):
        self.policy(PUBLISH, [self.role_reviewer])
        req = self.raise_request()
        with mock.patch("django_maker_checker.services._", side_effect=str.upper):
            with self.assertRaisesMessage(ApprovalError, "SAY WHAT NEEDS TO CHANGE."):
                services.decide(req.pk, self.checker1, Decision.REQUEST_CHANGES, "")

    def test_without_a_catalog_for_the_active_language_text_stays_english(self):
        self.policy(PUBLISH, [self.role_reviewer])
        with translation.override("fr"):
            req = self.raise_request()
            with self.assertRaisesMessage(ApprovalError, "You cannot approve or reject a request you raised."):
                services.decide(req.pk, self.maker, Decision.APPROVE)


class WhatStaysEnglish(EngineTestCase):
    def test_rule_history_is_recorded_in_english_whatever_language_is_active(self):
        spec = PolicySpec(workflow_type=WorkflowType.SINGLE, approvers=[f"r:{self.role_reviewer.pk}"])
        with translation.override("fr"):
            policy = policies.save_policy(policies.policy_for(self.tenant, PUBLISH), spec, by=self.admin)
        revision = policy.revisions.get()
        self.assertEqual(revision.snapshot["Workflow"], "Single Approver")
        self.assertEqual(revision.snapshot["Rule enabled"], "Yes")
