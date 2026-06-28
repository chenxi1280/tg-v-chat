# Release Gate Recheck: TG Private Relay V1 PostgreSQL Release Engineering

- release_id: dev-release-engineering-tg-private-relay-v1-pgsql-1
- message_id: product-release-gate-recheck-tg-private-relay-v1-pgsql-1
- reply_to_message_id: dev-release-engineering-tg-private-relay-v1-pgsql-1
- intake_id: intake-tg-private-relay-001
- related_version: tg-private-relay-v1
- from_agent: product
- to_agent: flow-supervisor
- level: L2
- evidence_level: E3
- status: blocked_until_release_actions
- release_gate: blocked_until_release_actions
- production_fixed: false
- done_status: product_accepted_not_released

## Product Decision

Product accepts that dev has addressed the previous release engineering blockers
at file/config level:

- PostgreSQL runtime contract exists and SQLite is rejected for normal runtime.
- Alembic migration workflow exists.
- GitHub Actions release workflow exists for `release` branch and manual dispatch.
- Dockerfile and server compose exist.
- Server compose uses the external infra-compose network and does not define an
  app-owned PostgreSQL container.
- Required environment variables are documented without real secrets.
- Bot, listener, worker, migration, and healthcheck runtime entries are defined.

Release gate is still not passed. The remaining blockers are release actions and
external proof, not Product Design or local service behavior:

- A real GitHub Actions run on the `release` branch has not been triggered or
  recorded.
- Docker image build is not proven locally because Docker daemon is unavailable.
- Online PostgreSQL migration against infra-compose database is unproven.
- Production deployment has not run.
- E4 Telegram production verification remains unproven.

## Product-Side Verification

- `python3 -c 'import subprocess; subprocess.run(["python3", "-m", "pytest"], timeout=60, check=True)'`: passed, 10 tests.
- `python3 -m compileall -q src tests migrations`: passed.
- `TG_V_CHAT_DATABASE_URL=postgresql+psycopg://... alembic upgrade head --sql`: passed and generated SQL using `PostgresqlImpl`.
- `.github/workflows/deploy-production.yml` YAML parse: passed.
- `docker-compose.server.yml` YAML parse: passed.
- `docker compose --env-file .env.example -f docker-compose.server.yml config`: passed.
- `git diff --check`: passed.

## Release Gate Status

- product_acceptance: passed at E3
- qa_validation: passed at E3
- release_engineering_files: ready at E3
- release_gate: blocked_until_release_actions
- production_fixed: false

## Required Release Actions

1. Trigger or provide a real GitHub Actions run from the `release` branch.
2. Prove PostgreSQL-backed checks in Actions.
3. Prove Docker image build and GHCR push.
4. Prove deploy job execution to the approved server target.
5. Prove migration against the infra-compose PostgreSQL database.
6. Run post-deploy smoke checks for bot/listener/worker health.
7. Route prod-diagnosis only after deployment evidence exists.
8. Complete E4 Telegram verification: authorization, Bot update ingestion,
   listener receive, outbound peer receipt, failover, idempotency, and
   observability.

## Routing

- next_agent: flow-supervisor
- requested_decision_owner: flow-supervisor / project owner / ops
- reason: Product cannot pass release gate without release-branch GitHub Actions,
  approved deployment, PostgreSQL migration evidence, and E4 production evidence.

Path note: original handoff path `/Users/xida/PycharmProjects/tg-v-caht` was a
typo; actual project directory is `/Users/xida/PycharmProjects/tg-v-chat`.
