django-maker-checker
====================

**Maker-checker (four-eyes) approval of sensitive changes for Django.**

When a user (the *maker*) performs a guarded action, such as creating a user, changing a credit limit or approving a
payment run, the change is **not applied**. It becomes an approval request that someone else (a *checker*) must
approve, under rules each tenant configures. Only then is the change applied, re-checked against the current state of
the data.

It is built for the controls that regulated businesses (banks, fintechs, back offices) are audited on:

- **The change is held, not just flagged.** The action is captured and applied only on approval. If the data changed
  meanwhile, the request fails instead of overwriting someone else's edit.
- **Segregation of duties is enforced.** A maker can never approve their own request, not as a superuser and not
  through a delegation. One person can't approve two steps.
- **Rules are data, not code.** Each tenant decides which actions need approval, by whom, in what workflow and how
  fast, without a deploy. Every rule change is versioned with who, when and why.
- **Nothing gets stuck.** Each step has an SLA with a reminder and escalation to a backup approver.
- **Everything is on the record.** Every request has a timeline, every decision is stored, and every event is passed
  to your audit log.
- **Concurrency-safe.** Decisions lock the row, and only one open request may exist per item.

The library is **headless**: it ships models, rules, services and read queries, and no UI. You build the screens your
product needs on its Python API.

.. toctree::
   :maxdepth: 2
   :caption: Getting started

   installation
   quickstart

.. toctree::
   :maxdepth: 2
   :caption: Guide

   concepts
   integration
   defining_actions
   rules
   building_a_ui
   comparison

.. toctree::
   :maxdepth: 2
   :caption: Reference

   settings
   api
   changelog

.. toctree::
   :maxdepth: 1
   :caption: Project

   development
