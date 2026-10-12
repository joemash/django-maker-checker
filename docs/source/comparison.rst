How it compares
===============

Several Django packages use the word "approval" for different problems:

.. list-table::
   :header-rows: 1
   :widths: 22 26 26 26

   * -
     - django-maker-checker
     - Workflow engines (e.g. django-approval-workflow)
     - Moderation (e.g. django-approval)
   * - What is approved
     - A pending change, applied on approval
     - An existing object moving through steps
     - An edit to a model, kept in a sandbox
   * - Who decides what needs approval
     - Tenant-configured rules (data)
     - Code, per flow
     - Code, per model
   * - Maker can't approve their own change
     - Enforced, including superusers and delegations
     - Not enforced
     - Privileged users bypass approval by default
   * - SLA and escalation
     - Built in
     - Tracked; you run the timeout loop
     - None
   * - Multi-tenant
     - Built in
     - No
     - No
   * - Rule history
     - Versioned, with who and why
     - No
     - No

Choose a **workflow engine** when you need rich routing: quorums, org-chart hierarchies, parallel tracks. Choose a
**moderation** package to review user-generated content before it is published. Choose **django-maker-checker** when
the requirement is a control: *nobody changes this alone, and we can prove it*.

Known limits
------------

- **No built-in condition language.** Settings such as "only above 10,000" go in a rule's ``conditions``; each action
  interprets them in ``matches()``, validates them in ``validate_conditions()`` and describes them for rule history in
  ``describe_conditions()`` (see :ref:`rules:Action-specific settings`). There is no generic rule builder: an action
  defines which settings it understands.
- **Routing is by named approvers.** One, any N, all, or ordered steps; there is no org-chart routing (e.g. "the
  maker's manager's manager").
- **English only so far.** Every user-facing message is translatable, but the package ships no translations yet;
  contributions are welcome (see :doc:`development`). Rule-history labels are stored, so they stay in English.
