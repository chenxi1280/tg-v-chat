# Release Gate Review: TG Private Relay V1

- release_id: dev-release-gate-tg-private-relay-v1-blocked-1
- message_id: product-release-gate-review-tg-private-relay-v1-blocked-1
- reply_to_message_id: dev-release-gate-tg-private-relay-v1-blocked-1
- intake_id: intake-tg-private-relay-001
- related_version: tg-private-relay-v1
- from_agent: product
- to_agent: flow-supervisor
- level: L2
- evidence_level: E3
- release_gate: blocked
- status: blocked
- production_fixed: false
- done_status: not_done

## Product Decision

Product accepts dev's Release Gate conclusion: `release_gate=blocked`.

The blocker is not local implementation quality. QA and Product Acceptance remain
valid at E3. The blocker is missing release and production prerequisites:

- No CI workflow or remote CI run. Required pattern: GitHub Actions triggered by
  `release` branch, following `tg-yunying`.
- No Dockerfile, compose file, deployment target, or build artifact release path.
  Required pattern: CI builds image and deploys via server compose.
- No production migration strategy. PostgreSQL is the required runtime database,
  and the current SQLAlchemy schema has no Alembic configuration or migration files.
- No production worker/listener process definition, supervisor/systemd/container
  config, health check, or restart policy.
- No verified Telegram production configuration for Bot token, three developer
  app slots, user sessions, and network/platform behavior.
- No E4 evidence for real Telegram authorization, Bot update ingestion, user
  session listener, outbound peer receipt, failover, idempotency, or observability.

## Fresh Product-Side Verification

- `python3 -c 'import subprocess; subprocess.run(["python3", "-m", "pytest"], timeout=60, check=True)'`: passed, 9 tests.
- `python3 -m compileall -q src tests`: passed.
- `git diff --check`: passed.
- Probe for `.github`, Dockerfile, compose, and Alembic config: none found.

## Required To Unblock Release Gate

1. Add CI workflow or provide remote CI evidence.
2. Define deployment target and build/release artifact path.
3. Add and verify PostgreSQL migration strategy. Runtime/release database must
   be PostgreSQL; release evidence cannot rely on SQLite.
4. Define worker/listener runtime: process manager, container or systemd unit,
   health check, restart policy, logs, and failure alerting.
5. Use the existing infra-compose PostgreSQL database/network, and provide
   production-safe environment wiring for:
   - `TG_V_CHAT_DATABASE_URL`
   - `TG_V_CHAT_SESSION_KEY`
   - `TG_V_CHAT_BOT_TOKEN`
   - `TG_V_CHAT_PRIMARY_API_ID`
   - `TG_V_CHAT_PRIMARY_API_HASH`
   - `TG_V_CHAT_STANDBY_1_API_ID`
   - `TG_V_CHAT_STANDBY_1_API_HASH`
   - `TG_V_CHAT_STANDBY_2_API_ID`
   - `TG_V_CHAT_STANDBY_2_API_HASH`
6. Run E4 production verification after an approved release path exists.

## Product Deployment Decision

- database_engine: PostgreSQL
- deploy_source_branch: `release`
- deploy_pipeline: GitHub Actions, following `tg-yunying`
- database_source: existing infra-compose PostgreSQL
- compose_network: `infra_default`
- release_handoff: product-resync-tg-private-relay-v1-pgsql-release

## Routing

- next_agent: flow-supervisor
- requested_decision_owner: project owner / ops
- reason: Product and dev cannot pass release gate without release infrastructure,
  production environment configuration, and E4 verification access.
