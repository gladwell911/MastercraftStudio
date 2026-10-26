# Remote slimming protocol and controlled tunnel deployment

This is a coordinated protocol upgrade. Source verification does not update the daily MC process, physical phone, or public endpoint. Do not restart the daily MC, clear histories or notification ledgers, drain old consumers, or alter OneDrive data.

## Channels and permissions

Clients use `zgwd.<pair>.rpc`. Reply subjects bind pair, device, session and request through SHA-256 segments. Subscribe and await PONG before publishing. The server validates the exact reply subject and negotiated epoch. Timeouts cancel subscriptions; uncertain business writes are not automatically resent.

Foreground hints and live files use uncaptured Core subjects. Execution hints identify owners and revisions. Visible execution pages fetch the latest 50 rows; older rows require explicit paging. Recent chat pages contain 30 summaries and initial histories contain 10 turns. Partial pages merge without deleting omitted owners. Long content has explicit owner/revision-bound continuations. Partial answers cannot acknowledge the complete answer as read.

The server owns `ZGWD_NOTIFY_<pair>` with limits of seven days, 32 MiB and 20,000 messages, and reads the limits back after updating. Compact projections preserve stable identities and carry their own hash. Canonical facts and full content remain in SQLite. Android pulls 50 records per batch with at most 100 pending acknowledgements. ACK does not mean read.

Production uses separate `mc` and `mobile` users. The mobile password is the pairing token; the desktop password is independently generated in application data. Mobile permissions allow scoped RPC, notification consumption and ACK, but deny stream mutation and legacy mixed-stream access. Legacy token-only clients require a coordinated upgrade.

## Recovery and migration

Capable peers negotiate `notification-feed-v1` and the database UUID `notification_feed_id`. Baselines and recovery checkpoints are scoped to pair/feed/domain/connection scope. A changed database UUID establishes a new authority baseline without clearing canonical posted/terminal/read deduplication. Broker incarnation changes only paging; a lower sequence on the same feed never proves replacement. A restored backup with the same UUID cannot be identified automatically. Previously terminal events do not become new notifications after upgrade. See [the current deployment](handoff.md) for installed evidence and remaining acceptance.

Android retains its installation baseline and posted ledger. It establishes current read/delete authority, then processes authoritative notification snapshots through the existing actor and ledger before pulling the new live consumer. Deliver-new is only an overlap mechanism; it does not replace offline recovery.

SQLite snapshots bind installation, pair, stream incarnation and business high-water H. Pages contain at most 100 metadata records and stay within the response budget. Background recovery throttles pages. Canonical final/outbox gaps are reconciled, and clear/delete authority suppresses stale generations. Each page persists migration phase and cursor. Only an explicit receipt after all records are processed advances the device checkpoint. Device processing and shared read cursors remain separate.

A stream rebuild changes incarnation and invalidates old continuations. Recovery restarts against authoritative state while preserving installation baseline and deduplication identity. Rollback must retain the new protocol and ledgers; restoring an old APK that reads legacy sequence one is unsupported.

## Tunnel ownership and diagnostics

`REMOTE_CONTROL_CLOUDFLARED_PROTOCOL` accepts auto, quic or http2. An optional `REMOTE_CONTROL_CLOUDFLARED_COMMAND` supplies a validated named-tunnel argv, executed without a shell. Never expose tokens or full command lines in shared diagnostics. An operating-system lease binds managed ownership to pair and tunnel identity. Stop only owned process handles; never kill all processes by image name.

Existing Windows services are managed only with explicit `REMOTE_CONTROL_CLOUDFLARED_OWNER_MODE=service`. Health distinguishes origin, DNS/TCP route, connector channels and public readiness. Restart requires healthy origin/network and confirmed zero owned channels for 60 seconds, with at most two attempts per ten minutes. Unknown channel state does not authorize restart. TCP reachability does not prove UDP health, and HTTP/2 cannot repair DNS.

Before deployment record cloudflared version, redacted startup source, origin, broker server_id and APK hashes. Compare QUIC and HTTP/2 on the same version/network for at least 15 minutes and 30 lightweight requests each. `scripts/diagnose_tunnel_path.py` probes only explicitly supplied addresses and does not modify services, proxy settings or production connectors. Change one variable at a time and distinguish DNS, UDP 7844, TCP 7844 and HTTP/WebSocket failures.

The user restarts daily MC. Then verify capabilities/server_id and use the existing data-preserving ARM64 physical-phone upgrade script. Public list p95, 20 controlled disconnects, an 8-12 hour soak, and physical TalkBack/lock-screen checks require separate deployment evidence. Isolated broker byte measurements and emulator tests do not substitute for those checks.

Token-file leases read only the explicitly supplied file internally and derive identity from the Cloudflare token tunnel UUID. Inline, equals-form, slash-format and copied-file representations of the same token share ownership. Secret rotation retains the lease when the tunnel UUID is unchanged. Credentials JSON uses TunnelID. Opaque tokens without a decodable tunnel UUID use a token digest; opaque-token rotation changes identity because continuity cannot be established. Credential contents and raw paths are excluded from failure diagnostics. Missing/unreadable credentials fail startup safely.

## Publication history maintenance (2026-10-24 source)

SQLite publication progress is durable per pair/domain and means broker publication, not phone consumption or read acknowledgement. Published outbox copies older than seven days may be removed only at/below that confirmed prefix, in one default-500 (maximum-1000) batch. Durable facts, authoritative notification recovery, user history and device cursors remain available. Existing outbox workers check before publication batches while active and run at most one background maintenance batch per five minutes; no worker activity means no cleanup; `REMOTE_CONTROL_OUTBOX_RETENTION=0` disables this maintenance. Busy writes yield rather than blocking the GUI. No physical database compaction is performed.

The existing notification stream remains bounded to seven days, 32 MiB and 20,000 messages, with limits checked after initialization. Legacy COMMAND/EVENT stream expiration remains disabled: the existing current-client snapshot recovery contract does not establish authoritative offline recovery for every legacy client. The no-client legacy publication fallback remains intact. No legacy retention compatibility claim is made by the current isolated tests.


## Database feed identity and recovery (2026-10-24 source)

`notification-feed-v1` advertises the persistent SQLite `meta.notification_feed_id` as `notification_feed_id` in hello/readiness. It survives reconnect, cleanup, restart and broker replacement. `notification_incarnation` identifies the broker stream only: changing it invalidates a snapshot cursor, not the established database baseline. A newly created database gets a distinct UUID. Lower numeric watermarks alone never establish a reset.

Feed-aware notification recovery keeps a separate device baseline/checkpoint per pair, installation and feed, leaving legacy progress and immutable canonical facts intact. Signed snapshot cursors and completion receipts select a snapshot bound to that same feed and stream; retired-feed requests are rejected. All durable transport deliveries, including full canonical fallback facts, bind the feed in a separately hashed projection with fact_hash retaining the original canonical hash. Old stored durable facts keep their original hash. The client establishes a fresh authoritative hello baseline once per feed, excludes older finals, then recovers new finals normally. Posted/read ledgers and pending read acknowledgements remain intact.

The feed field is optional for legacy clients and servers. Without the capability, legacy behavior remains and a database replacement cannot be inferred safely; upgrade both endpoints for feed-reset recovery. Restoring an older backup containing the same UUID cannot be detected automatically; this implementation provides no automatic restore/rotation boundary. Treat such a restore as an operator-managed database replacement with a fresh database identity before enabling recovery; never rotate merely because the high watermark decreased.

Verification uses isolated checkouts and temporary fake-builder targets. No daily MC restart, real package update or personal-device installation is part of this repair. Foreground service/store regression reproduces a retained high revision rejecting a fresh lower revision after feed replacement; that fixture does not prove the production foreground report has the same cause. Actual foreground and notification-shade acceptance remains post-deployment validation.

`mark_chat_read` may include `body.notification_feed_id`; an explicit identity must match the current database feed before any read mutation. An absent field retains legacy compatibility. Current native pending read targets retain their feed through this request boundary.
