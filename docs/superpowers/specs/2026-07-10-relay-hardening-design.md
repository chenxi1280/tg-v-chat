# tg-v-chat Relay Hardening Design

## Context And Approved Direction

The audit found that the repository exposes product objects for media relay, three session slots, failover events, and workers, but several production adapters stop at scaffold-level behavior. The approved direction is to preserve the original V1 promise instead of narrowing it to text-only or primary-only behavior.

## Considered Approaches

### 1. Narrow The Product Contract

Document the current runtime as text-only with one authorized session. This is the smallest code change, but it contradicts the user's approved product scope and is rejected.

### 2. Patch Individual Symptoms

Add status checks, call `send_file`, and start the existing worker shell. This is quicker, but leaves media durability, failover authorization, transaction rollback, error classification, and idempotency inconsistent. It is rejected because it would create another superficially green implementation.

### 3. Complete The Existing State Machine

Keep the current services and repositories, add the missing durable states and adapter boundaries, and implement each path test-first. This is the selected approach because it preserves the repository's structure while closing the product and data-flow gaps without introducing a new platform.

## Scope

### In Scope

- Enforce active SystemUser, active BoundTgAccount, and active ReplyMapping lifecycle before outbound delivery.
- Persist outbound dispatch lifecycle and failure information before returning user-visible results.
- Distinguish definitive session failures, business delivery failures, and ambiguous in-flight transport outcomes.
- Add independent primary, standby_1, and standby_2 authorization or reauthorization through the existing Bot wizard.
- Select and reconnect listener clients when the preferred healthy slot changes.
- Isolate one account's listener startup failure from other accounts.
- Run a real session health worker and expose role-specific process health.
- Download, persist temporarily, push, and reply with real photo and sticker bytes.
- Handle incoming and outgoing Telegram albums as ordered groups.
- Add missing relational constraints, timestamps, statuses, and migration coverage.
- Reconcile PRD, dataflow, structure, QA, and release-status wording with verified behavior.

### Out Of Scope

- Group, channel, topic, voice, video, GIF, file, contact, or location relay.
- CRM, assignment, tags, automatic replies, account farming, or risk-control evasion.
- Web administration UI.
- Production deployment, production migration, or E4 claims.
- Silent media-size caps or mock delivery success.
- A claim of cross-system ACID or unconditional exactly-once delivery.

## Architecture

### Domain And Persistence

`IncomingPrivateMessage` and `OutgoingReply` gain immutable media artifacts containing a storage key, file name, MIME type, byte size, media kind, and sequence. Telethon objects never cross into the service layer. A `MediaStore` writes downloads under `TG_V_CHAT_MEDIA_ROOT` through a `.part` file and atomic rename, and the database tracks artifact lifecycle as `staging`, `ready`, `sent`, `failed`, or `released`.

`ReplyMapping` gains `created_at`, `invalidated_at`, and enforced `active/expired` status. A time-based TTL is not introduced because the product has no approved duration or producer. Both `OutgoingReply` and `BotPushMessage` become lifecycle records with `pending`, `sending`, `sent`, `failed`, or `uncertain`, nullable Telegram result fields, a stable dispatch key, failure category, and failure reason. Album group dispatch uses the same lifecycle. Existing relationship columns receive explicit foreign keys.

Database uniqueness remains a concurrency guard, not proof of external exactly-once behavior. The implementation persists dispatch state before external I/O. An ambiguous result after a request may have reached Telegram becomes `uncertain` and is not automatically failed over or duplicated. Stable raw MTProto random ids are a separate future hardening step unless all text, media, and album adapters can reuse them consistently.

### Authorization And Session Slots

The Bot runtime injects an authenticator registry keyed by `DeveloperSlot`, instead of a single primary authenticator. Initial binding authorizes primary. Account detail exposes explicit `account.slot.bind:{account_id}:{slot}` and `account.slot.reauth:{account_id}:{slot}` callbacks. Each callback runs the same phone/code/2FA wizard against the selected slot's developer app; it never implicitly chooses a different slot.

Completing authorization upserts only the selected slot and increments its non-secret revision: primary becomes `active`; standby slots become `standby`. It never copies one developer app's session into another slot. Account status is deterministic: usable primary means `active`; unusable primary plus any usable standby means `degraded`; no usable slot means `reauth_required`; `disabled` and `deleted` are never automatically changed. A standby that has never been authorized is an attention item but does not degrade a usable primary.

### Outbound Delivery And Failover

Before sending, the service validates ownership and status, then acquires an explicit session-level PostgreSQL advisory lock on the current database connection, keyed by bound account id. Disable, reauthorization, and slot mutation flows use the same lock. It creates or reads the durable outgoing lifecycle row and commits the claimed `sending` state before external I/O. Row locks are not described as surviving commit; the session advisory lock remains tied to the live connection and is explicitly released in `finally` after terminal state persistence.

Dispatch recovery semantics are explicit: `pending` may be claimed once; `sending` means Telegram may have received the request; a stale `sending` observed after restart becomes `uncertain` and is not resent; `uncertain`, `failed`, and `sent` are terminal for the same Telegram update id. A new user reply has a new update id and can form a new explicit attempt.

Only a definitive session-layer failure can mark a slot failed and move to the next slot. Peer/content/permission/rate-limit failures become business delivery failures and do not poison session health. Pre-send connection failures may fail over; ambiguous in-flight failures become `uncertain` to prevent automatic duplicate sends. All failure rows and failover events are committed before the exception is surfaced to the Bot router.

### Listener And Worker Runtime

The listener uses incoming-only events. Non-grouped messages use `NewMessage(incoming=True)`; grouped media use `Album(incoming=True)`, with album members enumerated in Telegram order. A grouped `NewMessage` is ignored so an album is not pushed twice.

Each listener binding has a fingerprint containing account id, slot id, and non-secret session revision. The refresh loop disconnects removed or changed bindings, reconnects disconnected clients, and catches startup failure per account. A failed account cannot terminate listeners for other accounts. Fingerprints and logs never include session plaintext or encrypted-session identity.

The worker periodically verifies authorized session slots and updates status using the same error classification. It releases files for `sent` and deterministic `failed` artifacts after terminal metadata is committed; `pending`, `sending`, and `uncertain` artifacts remain. An orphan `.part` file is removed only when no database artifact row references its storage key and a non-blocking exclusive file lock proves no writer is active. Bot, listener, and worker processes maintain role-specific heartbeat files; container health checks require both PostgreSQL connectivity and a fresh heartbeat for that role.

### Media Flow

For incoming photo or sticker events, the user-session listener downloads into the shared media spool before crossing into the synchronous service. The service persists artifact metadata and the ordered relay group before Bot I/O. The Bot adapter uploads the real files, preserving album order, and returns one Bot message id per relay item so each item receives its own mapping. A failed item fails the whole album; partial albums are not presented as success.

For Bot replies, the Bot process downloads photo or sticker files into the same spool and builds the immutable outgoing media group. The selected user-session adapter sends text with `send_message` and media with the Telethon media API. Incoming or outgoing unsupported media is rejected explicitly before a success mapping is created; it is never converted to placeholder text. File names are metadata only, path separators are removed, spool directories use owner-only permissions, and no silent size cap is introduced.

### Mapping Lifecycle

Mappings are invalidated when the bound account is disabled or deleted, or when the source relay is explicitly invalidated. A reply to an inactive, disabled, deleted, expired, or non-owned mapping fails before any sender call. Successful account deletion removes session secrets while retaining historical relay metadata through soft-deleted account identity. Time-based expiry remains unimplemented and explicitly unproven until a product duration is approved.

## Error Handling

- `SessionFailure`: authorization revoked, invalid auth key, or definite pre-send connection/session failure; eligible for failover.
- `DeliveryFailure`: peer, content, permission, rate-limit, unsupported-media, or Telegram business rejection; no failover.
- `DeliveryUncertain`: request may have reached Telegram but no result was received; persist uncertain state and do not resend automatically.
- Listener startup errors are logged with account and slot identity and do not stop other bindings.
- Health checks fail when the role loop is stale even if PostgreSQL remains reachable.

## Testing Strategy

Every behavior change follows red-green-refactor. Unit and adapter-contract tests cover:

- Disabled/deleted/system-disabled/inactive-or-expired-mapping replies make zero sender calls.
- All-session failure persists failed slots and exhausted events after the surfaced exception.
- Business delivery errors do not change session status or create failover events.
- Ambiguous delivery becomes uncertain and does not resend on duplicate Bot updates.
- Bot push and album dispatch use the same lifecycle; crash recovery never automatically repeats stale `sending` pushes.
- Primary, standby_1, and standby_2 authorization store independent encrypted sessions.
- Listener selects standby when primary is failed and reconnects when the selected slot changes.
- One bad binding does not disconnect healthy listeners.
- Worker runs real checks and role health fails on stale heartbeat.
- Photo, sticker, incoming album, and outgoing album use real spool artifacts and preserve order.
- Grouped NewMessage events are ignored in favor of Album events.
- Migration upgrade SQL contains the new columns, indexes, and foreign keys.
- PostgreSQL online tests prove concurrent claim behavior, advisory locking, orphan preflight, and foreign-key rejection; without that environment these remain unproven.
- Product and index checklists reflect only automated evidence; real Telegram and production remain E4-unproven.

## Rollout Boundary

Completion in this branch means local tests, compile checks, migration SQL checks, compose interpolation, and independent subagent review pass. It does not mean production is fixed. Release and E4 verification remain separate gates.
