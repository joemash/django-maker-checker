Development
===========

Contributions are welcome. The full guide is in
`CONTRIBUTING.md <https://github.com/joemash/django-maker-checker/blob/main/CONTRIBUTING.md>`_; in short:

.. code-block:: bash

   git clone https://github.com/joemash/django-maker-checker.git
   cd django-maker-checker
   python -m venv .venv && source .venv/bin/activate
   pip install -e ".[dev]"
   pytest

The tests use SQLite by default; set ``DATABASE_URL=postgres://user:password@host:5432/name`` to run them on
PostgreSQL, as CI does. ``tox`` runs everything CI runs, including the Python × Django matrix.

The tests as a specification
----------------------------

Each test module describes one area of the library, and its test names state the rules, so reading the tests is a
way to learn the library. ``tests/README.md`` gives a reading order, from defining actions to the host contract.

Building the docs
-----------------

.. code-block:: bash

   pip install -r docs/requirements.txt
   make -C docs html          # output in docs/build/html

Releasing
---------

1. Move the "Unreleased" changelog entries under the new version and bump ``django_maker_checker.__version__``.
2. Merge to ``main``, then publish a GitHub release tagged ``vX.Y.Z``. The release workflow publishes the package to
   PyPI, and Read the Docs builds the documentation for the tag.
