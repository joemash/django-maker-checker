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

Add the app and point the engine at your tenant model, role model and adapter:

.. code-block:: python

   INSTALLED_APPS = [
       ...,
       "django_maker_checker",
       "approvals_integration",  # your app holding the adapter and guarded actions
   ]

   APPROVALS_TENANT_MODEL = "accounts.Organisation"
   APPROVALS_ROLE_MODEL = "accounts.Role"
   APPROVALS_ADAPTER = "approvals_integration.adapter.Approvals"

Then migrate:

.. code-block:: bash

   python manage.py migrate

The engine's app label is ``approvals``, so its tables are named ``approvals_*``. All settings, including the optional
ones, are listed in :doc:`settings`.

If your tenant or role model is created after your app's first migration, see
:ref:`integration:Migration order`.
