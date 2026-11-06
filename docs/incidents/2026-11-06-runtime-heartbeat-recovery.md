# 2026-11-06 runtime heartbeat recovery

## Observed production failure

The affected host is `silicon-valley-production-server`, not the Shanghai test
middleware host. All three containers used image revision `f83f45e`.

- Listener exited at 10:47:18 Asia/Shanghai after `OSError: [Errno 28] No space
  left on device`. Docker could not recreate its overlay mount for the same reason.
- Worker exited at 10:49:01 with the same disk-full failure. Its earlier logs also
  show `ValueError: sleep length must be non-negative` in `_sleep_with_heartbeat`.
- Bot stayed running but its heartbeat file was 0 bytes, and every retained
  healthcheck failed with `Invalid isoformat string: ''`.
- At recovery time the filesystem had approximately 7 GiB available and 22% inode
  usage. No additional disk cleanup was performed for this repair.

## Root causes and repair

`RoleHeartbeat.beat` opened the live heartbeat with truncation before writing.
Disk exhaustion could leave an empty file; ordinary concurrent reads could also
observe the truncation window. Write the timestamp to a sibling temporary file
and atomically replace the live heartbeat after successful completion. Write
errors remain visible, and the last complete timestamp still becomes stale.

`_sleep_with_heartbeat` checked the deadline and calculated the sleep duration
using two different clock reads. Crossing the deadline between those reads could
produce a negative duration. Calculate the remaining duration once per iteration
and exit when it is non-positive.

Once free space was confirmed, only `tg-v-chat-listener`, `tg-v-chat-worker`, and
`tg-v-chat-bot` were started/restarted. At 11:36 Asia/Shanghai all three were
running and healthy. The old listener/worker Docker log streams also reported
malformed JSON / unexpected EOF; logs from the replacement containers must be
checked after deployment.

## Verification boundary

- Both new regression tests fail on the original implementation and pass with
  the repair.
- Local E3: 267 passed, 8 skipped. PostgreSQL integration tests were not run
  locally because no dedicated test database was configured; SQLite unit tests
  are not production database evidence.
- Compile, Compose configuration validation, and `git diff --check` passed.
- GitHub Actions deployment and replacement-container E4 observation are pending
  at the time of this source record.
- Real Telegram private-message intake, forwarding, and reply delivery have not
  been manually accepted by this repair. No diagnostic test messages were sent.
