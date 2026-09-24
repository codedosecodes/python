# SignFlow

This repository retains its original Python learning notebooks and now includes
the foundation of **SignFlow**, an original, white-label-ready electronic
signature platform built with Django.

The audited architecture, delivery sequence, security model, and acceptance
criteria are documented in
[`docs/ESIGNATURE_IMPLEMENTATION_PLAN.md`](docs/ESIGNATURE_IMPLEMENTATION_PLAN.md).

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python manage.py migrate --run-syncdb
python manage.py createsuperuser
python manage.py runserver
```

Local development defaults to SQLite. Production switches to PostgreSQL when
`DATABASE_URL` is present and reads connection details from `POSTGRES_DB`,
`POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, and `POSTGRES_PORT`.
Production must also set `DJANGO_SECRET_KEY`, `DJANGO_DEBUG=false`, and
`DJANGO_ALLOWED_HOSTS`.

## Checks

```bash
python -m unittest tests.test_domain
python manage.py test
python -m compileall -q esign signflow tests
python manage.py check
python manage.py makemigrations --check --dry-run
```

The initial application sprint provides secure account creation/login/password
recovery, organizations and memberships, tenant resolution, the core relational
domain model, a centrally branded responsive shell, and a data-backed home
dashboard. Controls for later workflows are deliberately disabled and labelled
until their underlying services are implemented.

> The notebooks are educational artifacts and are not imported by the web app.
