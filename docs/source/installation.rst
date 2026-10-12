Installation
============

Requirements
------------

- Python 3.11 or newer
- Django 5.2, 6.0 or 6.1
- `django-fsm-2 <https://github.com/django-commons/django-fsm-2>`_, installed automatically

Install the package:

.. code-block:: bash

   pip install django-maker-checker

To run the SLA job as a Celery task, install the ``celery`` extra:

.. code-block:: bash

   pip install "django-maker-checker[celery]"

Without Celery, run the job from cron with ``python manage.py process_approval_slas`` (see :doc:`concepts`).

Databases
---------

=====================  ==========================================================================================
Database               Support
=====================  ==========================================================================================
PostgreSQL             Recommended for production. Every guarantee holds.
SQLite                 Fine for development and tests. ``select_for_update`` is a no-op there; SQLite serialises
                       writes itself, but isn't suited to many concurrent users.
MySQL / MariaDB        Not recommended. They ignore Django's conditional unique constraints, so "only one open
                       request per item" is not enforced.
=====================  ==========================================================================================

Settings
--------

Add the app to ``INSTALLED_APPS`` and migrate:

.. code-block:: python

   INSTALLED_APPS = [
       ...,
       "django.contrib.auth",
       "django.contrib.contenttypes",
       "django_maker_checker",
   ]

.. code-block:: bash

   python manage.py migrate

That is enough for a **single-tenant** project: the engine uses Django's own users, groups and permissions through
its built-in adapter (see :ref:`integration:The built-in adapter`).

For a **multi-tenant** project, or one with its own roles or permission system, point the engine at your models and
your adapter:

.. code-block:: python

   APPROVALS_TENANT_MODEL = "accounts.Organisation"
   APPROVALS_ROLE_MODEL = "accounts.Role"
   APPROVALS_ADAPTER = "approvals_integration.adapter.Approvals"

Decide this before the first ``migrate``: like ``AUTH_USER_MODEL``, the tenant and role models can't be changed
later. The engine's app label is ``approvals``, so its tables are named ``approvals_*``. All settings are listed in
:doc:`settings`.

If your tenant or role model is created after your app's first migration, see
:ref:`integration:Migration order`.
