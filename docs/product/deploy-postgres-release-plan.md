# TG Private Relay V1 PostgreSQL Release Plan

- message_id: product-resync-tg-private-relay-v1-pgsql-release
- intake_id: intake-tg-private-relay-001
- related_version: tg-private-relay-v1
- from_agent: product
- to_agent: dev
- level: L2
- evidence_level: E2
- status: ready_for_dev
- release_gate: blocked

## Product Decision

TG Private Relay V1 must use PostgreSQL as the application database for release
and production. The app must not ship with SQLite as the runtime database.

Deployment must follow the `tg-yunying` production pattern:

- GitHub Actions deploy workflow is triggered from the `release` branch.
- Checks run in GitHub Actions with a PostgreSQL service.
- Images are built in CI and pushed to GHCR.
- Server deployment uses Docker Compose.
- The service joins the existing infra-compose network/database instead of
  starting an app-owned PostgreSQL container.
- Runtime database is injected through `TG_V_CHAT_DATABASE_URL`.

## Database Contract

- database_engine: PostgreSQL
- runtime_env_var: `TG_V_CHAT_DATABASE_URL`
- expected_url_shape: `postgresql+psycopg://<user>:<password>@<infra-postgres-host>:5432/<database>?connect_timeout=3`
- production_database_owner: infra-compose
- application_database_owner: tg-v-chat app user/schema
- sqlite_runtime_allowed: false
- sqlite_test_only_allowed: only for isolated unit tests if explicitly marked not release evidence
- release_evidence_database: PostgreSQL only

## Deploy Contract

Expected files or equivalent:

- `.github/workflows/deploy-production.yml`
- `Dockerfile`
- `docker-compose.server.yml`
- `.env.example`
- deploy helper script if needed, following `tg-yunying/deploy/docker-env.sh`

The server compose should follow the `tg-yunying` shape:

- require image env vars instead of building on the server
- require `TG_V_CHAT_DATABASE_URL`
- require `TG_V_CHAT_SESSION_KEY`
- require `TG_V_CHAT_BOT_TOKEN`
- require three Telegram developer app slot env pairs
- use `networks: infra_default`
- define worker/listener process and health check

## GitHub Actions Contract

- trigger: push to `release`
- also allow manual `workflow_dispatch`
- checks job:
  - Python setup
  - PostgreSQL service
  - install project with dev dependencies
  - run compileall
  - run pytest against PostgreSQL
- build job:
  - build image
  - push GHCR image
- deploy job:
  - validate SSH/deploy secrets
  - copy/update compose/env files on server
  - pull image and restart service
  - run health check or smoke check

## Migration Contract

- Add Alembic or an equivalent explicit migration workflow before release gate
  can pass.
- Release gate must prove schema creation/migration against PostgreSQL.
- No production deployment may rely only on `Base.metadata.create_all`.

## Release Gate Unblock Criteria

- PostgreSQL CI passes.
- Docker image builds.
- Server compose references infra-compose database/network.
- Migration workflow exists and is verified on PostgreSQL.
- Worker/listener runtime is defined.
- Required env vars are documented without real secrets.
- E4 production verification can be routed after deploy.
