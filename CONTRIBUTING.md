# Contributing

Thanks for helping improve django-maker-checker. Bug reports, documentation fixes and pull requests are all welcome.

## Before you start

- For anything larger than a small fix, open an issue first so we can agree on the approach.
- Security problems: please don't open a public issue; see [SECURITY.md](SECURITY.md).

## Development setup

```bash
git clone https://github.com/joemash/django-maker-checker.git
cd django-maker-checker
python -m venv .venv && source .venv/bin/activate   # or: uv venv && source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install
pytest                               # the multi-tenant test project
pytest --ds tests.settings_single    # the single-tenant test project
```

The tests use SQLite by default. To run them against PostgreSQL, as CI does:

```bash
docker run -d --rm --name dmc-pg -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_DB=maker_checker -p 55432:5432 postgres:17
DATABASE_URL=postgres://postgres@localhost:55432/maker_checker pytest
```

`tox` runs what CI runs: linting, code quality, security checks and the Python × Django test matrix
(`tox -e py313-dj61`, for example, runs a single combination).

## Guidelines

- **The tests are the specification.** Each module in `tests/` describes one area of the library, and test names
  state the rules. Add or update a test that states the behaviour you are adding or fixing; `tests/README.md` says
  where each area lives.
- **Keep the engine headless and host-agnostic.** It must not import anything beyond Django, django-fsm and the
  standard library (Celery only in `tasks.py`); `tests/test_host_contract.py` enforces this. Anything about a host's
  users, roles, permissions, notifications or audit goes through `ApprovalsAdapter`.
- **Write through the services.** State changes go through `services` and `policies`, which lock rows and record the
  audit trail.
- **Comments explain why.** Describe the current behaviour and its reason; leave history to git.
- **Migrations:** generate them with `python -m django makemigrations approvals --settings=tests.settings` and keep
  them backwards compatible.
- **Docs** live in `docs/source` (reStructuredText, built with Sphinx and published on Read the Docs). Build them
  with `pip install -r docs/requirements.txt && make -C docs html`; CI fails on any Sphinx warning.
- Code style is enforced by ruff (`ruff check` and `ruff format`); coverage must stay at or above 90%.

## Pull requests

- One logical change per pull request, with a clear description of what and why.
- Add an entry under "Unreleased" in `CHANGELOG.md` for user-visible changes.
- Changes to the adapter contract, migrations or public function signatures need an upgrade note in the changelog.

## Releasing (maintainers)

1. Move the "Unreleased" changelog entries under the new version and bump `django_maker_checker.__version__`.
2. Merge to `main`, then publish a GitHub release tagged `vX.Y.Z`. The release workflow builds the package and
   publishes it to PyPI through trusted publishing.

The project follows [Semantic Versioning](https://semver.org).
