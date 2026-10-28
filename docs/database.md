# Database setup and compatibility

The SQLAlchemy storage layer supports SQLite for local development and PostgreSQL for concurrent use. The receiver, event reducer and authorization projection are shared by both backends.

## Choosing a database

SQLite is the default and needs no separate server. PostgreSQL is the recommended deployment database when several users and concurrent experiments share one application. Accounts, roles, sessions, messages and ordered run events fit a relational model; PostgreSQL provides transactions, constraints, row locking and multi-version concurrency control. These capabilities are described in the [official PostgreSQL overview](https://www.postgresql.org/about/).

The environment settings are resolved in this order:

1. Non-empty `AUCTION_DATABASE_URL`, such as `postgresql+psycopg://USER:PASSWORD@HOST:5432/DATABASE`.
2. `AUCTION_DB_PATH`, defaulting to `data/auction.sqlite3`.

The app and administrator CLI must use the same setting. Special characters inside connection URL credentials must be percent-encoded. Never commit a URL containing a real password.

## Existing SQLite installations

Keep the existing SQLite file and start the updated backend with its original `AUCTION_DB_PATH`. Startup creates the new authentication tables alongside the existing run, event and message tables. It does not delete or rewrite legacy experiments.

Legacy runs have no bidder account assignments, so only administrators can access them. Create an administrator with:

```bash
uv run python -m backend.auction_sim.admin create admin
```

No automatic migration moves SQLite accounts or experiments into PostgreSQL. Changing the connection setting selects another database; the old SQLite file remains available. Export completed experiments before planning a separate migration. A JSON export is an audit record, not a database backup or an implemented import format.

## PostgreSQL Compose

`compose.postgres.yaml` is a standalone alternative to the default SQLite Compose configuration. It starts PostgreSQL 18 and the same application image. Provide `POSTGRES_PASSWORD` in a local `.env` file. The example file intentionally has no password value. A generated hexadecimal password, such as the output of `openssl rand -hex 32`, is suitable for the URL interpolation used by this Compose file.

```bash
docker compose -f compose.postgres.yaml up --build
docker compose -f compose.postgres.yaml exec simulator python -m backend.auction_sim.admin create admin
```

The application is published only at `127.0.0.1:8000`. PostgreSQL has no published host port and is reached through the private Compose network. The database and account password configured by the PostgreSQL image are separate from application user passwords.

PostgreSQL 18 stores data under a versioned directory beneath `/var/lib/postgresql`, so the named volume mounts that parent directory. This follows the [official PostgreSQL Docker image guidance](https://hub.docker.com/_/postgres). Do not reuse the older `/var/lib/postgresql/data` mount convention for this configuration.

The PostgreSQL image's initialization environment variables apply only when its data volume is empty. Editing `.env` later does not rotate an existing database password. Maintain the volume and use database administration tools for credential changes and backups. For a managed or public deployment, provision an application-scoped database role and configure the managed database's TLS connection requirements.

## Concurrency and transactions

SQLite serializes writes with `BEGIN IMMEDIATE`. PostgreSQL mutations lock the run row before selecting its next actor and committing the resulting snapshot and event. Concurrent requests can therefore advance different runs while serialized writes prevent duplicate settlement of the same run. Reader transactions use repeatable-read snapshots on PostgreSQL so a request projects a consistent state and event history.

## Integration tests

The normal test suite uses temporary SQLite databases. PostgreSQL tests are enabled only when `AUCTION_TEST_DATABASE_URL` is present:

```bash
# Set AUCTION_TEST_DATABASE_URL to a dedicated test database in your environment.
uv run pytest backend/tests/test_postgres.py
```

These tests create a uniquely named schema per test, then remove only that schema. The test account therefore needs permission to create schemas. Use a dedicated test database. Without the variable, PostgreSQL tests are skipped rather than reported as having passed. The GitHub Actions PostgreSQL job starts a disposable PostgreSQL 18 service and supplies this setting.
