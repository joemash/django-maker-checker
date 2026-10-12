How it compares
===============

Several Django packages use the word "approval" for different problems:

=======================  ==================================  ===================================  ============================
                         django-maker-checker                Workflow engines (e.g.                Moderation (e.g.
                                                             django-approval-workflow)             django-approval)
=======================  ==================================  ===================================  ============================
What is approved         A pending change, applied on        An existing object moving through     An edit to a model, kept in
                         approval                            steps                                 a sandbox
Who decides what needs   Tenant-configured rules (data)      Code, per flow                        Code, per model
approval
Maker can't approve      Enforced, including superusers and  Not enforced                          Privileged users bypass
their own change         delegations                                                               approval by default
SLA and escalation       Built in                            Tracked; you run the timeout loop     None
Multi-tenant             Built in                            No                                    No
Rule history             Versioned, with who and why         No                                    No
=======================  ==================================  ===================================  ============================

Choose a **workflow engine** when you need rich routing: quorums, org-chart hierarchies, parallel tracks. Choose a
**moderation** package to review user-generated content before it is published. Choose **django-maker-checker** when
the requirement is a control: *nobody changes this alone, and we can prove it*.

Known limits
------------

- **No condition language.** Thresholds such as "only above 10,000" go in a rule's ``conditions`` and are read by the
  action's ``matches()``.
- **Routing is by named approvers.** One, any N, all, or ordered steps; there is no org-chart routing (e.g. "the
  maker's manager's manager").
- **English only so far.** Every user-facing message is translatable, but the package ships no translations yet;
  contributions are welcome (see :doc:`development`). Rule-history labels are stored, so they stay in English.
