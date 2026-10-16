# Remote slimming source validation (2026-10-16)

## Matrix and evidence boundaries

| Contract | Source / isolated evidence | Remaining deployment evidence |
|---|---|---|
| Legacy backlog independence, transient RPC and broker ACL | `test_remote_slim_broker_integration.py`: real broker, 100,000 retained records, 40,000 pending, 1,002 scoped RPCs; legacy counters unchanged, zero stored commands; notify limits read back and mobile stream mutation denied | Verify capabilities and server_id after the user restarts daily MC |
| Traffic reduction | `test_remote_slim_workload.py`: frozen baseline Git transport vs current transport, identical canonical dataset (100 chats, 100 turns/chat, 50,000 execution facts, 20 notifications); retained broker seed excluded from measured mobile connection; old consumer actually replays retained facts; same 610 query trace | This is a compressed logical 600-second isolated plain-NATS trace, not 600 seconds of public TLS traffic or a soak |
| Snapshot migration, incarnation, clear/delete, delayed outbox and full completion | `test_notification_sync.py`, existing read-state suites and native actor/ledger suites; material snapshots survive reopening, no checkpoint before last page, stale owner/content cursors rejected | Full crash-stage emulator instrumentation and physical upgrade retention must be recorded separately |
| Recent lists, partial merging, execution tails, long content and retry | MC collection tests, Dart service/store/observer tests, normal UI refresh retry and long-content menu widget tests; Local DualDevice real UI passed | Physical keyboard/TalkBack focus and old-notification routing should be exercised on installed release |
| Tunnel ownership, protocol argv, sustained failure and restart budget | `test_tunnel_policy.py`, runtime and actual main lifecycle tests; diagnostic script accepts only explicitly supplied paths | Same-version/network QUIC vs HTTP/2, at least 15 minutes and 30 samples each; public list p95; 20 controlled disconnects; 8-12 hour soak |

## Results

The current isolated compressed workload uses the actual production SQLite collection worker and registered history handler. For the old history page it executes the frozen baseline handler AST against the same temporary ChatStore; the old full collection handler uses the compatibility full-list path. Both sides retain identical canonical data. Broker seed is outside the measured mobile connection. The trace invokes command routing directly, so separate scoped-RPC tests remain necessary.

Current measured socket bytes: 479,036,858 -> 13,732,256 (97.1334% reduction). Application payload: 473,109,751 -> 13,586,198. Broker storage: 475,590,621 -> 23,831 bytes; records: 50,630 -> 20. Both sides retained all 20 required notifications. Run: 1 passed in 75.88 seconds. This is a compressed logical 600-second isolated plain-NATS trace, not wall-clock/public-TLS traffic or a soak. Socket accounting excludes IP/TCP headers.

Historical superseded fixture evidence: the previous pre-sliced synthetic response callbacks measured 99.6793% reduction (438,196,889 -> 1,405,465 socket bytes). Those values do not establish production-handler performance and must not be used as current acceptance evidence.

The separate production-handler broker fixture measured 1,002 real Core RPC exchanges and role permissions. The large workload fixture invokes command routing directly and measures returned broker traffic; these scopes must not be confused.

Latest implementation checks:

- MC: `.venv/Scripts/python.exe -m pytest tests/test_notification_sync.py tests/test_remote_nats_unit.py tests/test_remote_nats_protocol.py tests/test_chat_read_state.py tests/test_remote_slim_broker_integration.py tests/test_tunnel_policy.py tests/test_nats_runtime.py tests/test_main_remote_nats_unit.py -q -k 'not archived_mobile_result_v2_outbox_keeps_interleaved_owners_and_unique_finals'` -> 103 passed, 1 deselected. The deselected archived/interleaved-owner test is a recorded preexisting baseline failure; it is not counted as a pass.
- MC: `.venv/Scripts/python.exe -m pytest tests/test_remote_slim_workload.py -q -s` -> 1 passed, 21.90 seconds.
- RC: `flutter test --no-pub test/remote_nats_client_test.dart test/remote_nats_chat_service_test.dart test/remote_session_store_test.dart test/visible_execution_observer_test.dart test/chat_list_refresh_cue_test.dart` -> 139 passed.
- RC: `flutter analyze --no-pub lib/main.dart lib/remote_nats_chat_service.dart` -> no issues.
- Both repositories: `git diff --check` -> success (existing LF/CRLF conversion warnings only).
- Parent-run Local DualDevice R6 -> all tests passed in 31 seconds, exit 0; evidence `../../_bmad-output/implementation-artifacts/slim-parent-dual-local-r6-20261016/flutter-private.log`. R6 predates the subsequent actionable/failure eligibility and deadline amendments; unit verification covers those amendments.

Android final unit verification including the last actor amendment passed BUILD SUCCESSFUL in 20 seconds; parent evidence: `slim-parent-native-complete-20261016.log`. Final independent checks are recorded by the parent workflow with exact code-state evidence.

## QA transport findings

The dedicated QA emulator required a validated default network. A Java Socket probe showed emulator NAT 10.0.2.2 accepted a connection but returned EOF before HTTP upgrade, while an owned adb reverse to loopback returned HTTP 101. No specific firewall/TUN cause was established. The DualDevice runner now allocates scoped reverse mappings with cleanup. Production network guards remain active. Parent QA setup restored original captive portal/mobile data and Wi-Fi enabled state after each run.

No daily MC restart, physical installation, production connector mutation, ledger clearing or OneDrive mutation was performed. Local source verification does not authorize claims of deployed public-network stability.

Token-file ownership follow-up: `.venv/Scripts/python.exe -m pytest tests/test_tunnel_policy.py tests/test_main_unit.py -q -k 'tunnel_policy or cloudflared'` -> 26 passed, 983 deselected in 5.18 seconds. Covers supplied token-file command through main process startup/cleanup, equivalent path/equals forms, copied credentials, same-tunnel secret rotation, competing lease denial and missing-file safe failure. `git diff --check` passed.

## Review correction verification

- Snapshot/protocol/transport/runtime/diagnostic focused files: 64 passed in 12.44 seconds. Snapshot follow-up after processed-floor and canonical-reference corrections: `pytest tests/test_notification_sync.py -q` -> 10 passed in 6.32 seconds. Includes abandoned snapshots, unchanged-H reuse, completion without backlog replay, 350 offline actions and a 65 MiB canonical answer with a reference under 1 KiB in disposable storage. Snapshot construction batches 100 rows outside a WAL read transaction's write lock; retained snapshots are capped at 32 and abandoned snapshots expire after one day.
- `flutter test --no-pub test/remote_nats_client_test.dart test/remote_nats_chat_service_test.dart test/chat_list_refresh_cue_test.dart` -> 98 passed. Covers actual transient mutating RPC queued behind delayed PONGs, initial recent list before blocked authority, pinned/running sections outside recent, stale search cursor restart, and real service long-answer append/cursor/wrong-owner/read-sequence behavior.
- `flutter test --no-pub test/widget_test.dart --plain-name 'expanded projection rows survive a visible execution hint refresh'` -> 1 passed. This uses real ExecutionLogPage older expansion and observer hint refresh; it does not exercise a long execution chunk expansion.
- Native unit: `gradlew.bat :app:testDebugUnitTest '-Pkotlin.incremental=false' --tests 'com.example.zhuge_qa.RemoteBackgroundNotificationActorTest' --console=plain` -> BUILD SUCCESSFUL in 38 seconds. Cursor reset tests cover durable reset, failed preference commit and revoked owner without changing ledger/baseline.
- Native instrumentation, ANDROID_SERIAL=emulator-5570: `gradlew.bat :app:connectedDebugAndroidTest '-Pkotlin.incremental=false' '-Pandroid.testInstrumentationRunnerArguments.class=com.example.zhuge_qa.RemoteChatReadStateTest#serverCompactReadProjectionMergesAndCancelsCoveredNativeNotification' --console=plain` -> 1 passed, BUILD SUCCESSFUL in 45 seconds, verified Codex_Dual_Control_API_35. Test uses isolated preferences and cancels its own notification ID in finally. Evidence: `slim-review-native-read-test-20261016.log`.
- Shared compact-read fixture: `rc/android/app/src/androidTest/assets/compact_read_changed.json`, generated by actual MC compact_notification, compared byte-for-byte structurally by Python protocol test, then fed into real native parse/read merge/notification cancellation.

Remaining focused coverage limit: a controlled 100-row snapshot writer-interleaving test was not added. Final full affected-suite and dual UI checks belong to the parent verification run. The original projection semantics widget was observed failing its old exact load-count assertion before the new hint assertion (expected 2, actual 5); its original behavior/assertions were preserved, and only the new focused expansion regression was reported as passing.

Final critical regressions: real client owner A -> B -> empty -> event cancellation verifies exact SHA-256 subject, cancelled old SUB, suppressed hidden-owner delivery and zero active subscription leak; client file 20 passed. The queued mutating RPC test explicitly releases the expired request own PONG barrier after timeout and still observes zero PUB. Both real ExecutionLogPage older expansion and long-step chunk expansion survive visible tail hints (one focused widget pass each). The long-step regression exposed a second tail replacement path; same-owner/revision expansion preservation now covers both paths. Production registered history handler with escaped long answer/question metadata passes under 128 KiB and retains continuation/older cursors.

The final authority watermark is separate from business execution progress. Read-only snapshots reuse unchanged authority state even when execution facts advance the business sequence. Snapshot focused follow-up: 11 passed in 6.61 seconds; RPC collection focused follow-up: 2 passed. Execution-only authority-cache reuse is covered directly.

Final cache-key correction: authority_high_sync_sequence is an independent persistent canonical authority revision, not a business-sequence arithmetic value. SQLite triggers advance it for owner insert/delete, generation, readable-answer and read-state changes, plus owner revision changes. Thus empty chat create/delete invalidate cached inventories even without durable transport facts, while execution-only progress reuses authority snapshots. Foreground hints never advance the reconciled authority revision; only a complete successful inventory does. Snapshot final focused run: 13 passed in 6.94 seconds, including same-business-H empty-owner create/delete and raw text beginning with either internal cache marker. Raw cache marker prefixes are escaped, so ordinary user text cannot become an internal source reference.

## Final coordinator verification after review repairs

Final source checks: MC core 117 passed/1 explicitly excluded baseline failure; tunnel/runtime 41 passed; Flutter affected suites 144 passed; both real execution older/long-chunk expansion widgets passed; 13-file analyze clean; Android JVM 116 passed; native compact-read merge/cancel instrumentation passed on dedicated QA AVD. Controlled WAL writer-interleaving regression passed independently. Final Local DualDevice UI passed in 35 seconds, with owned fixtures cleaned and QA network settings restored.

Final production-handler workload received socket bytes 479,028,462 -> 13,732,293 (97.1333% reduction), payload 473,101,354 -> 13,586,235, broker bytes 475,582,224 -> 23,831 and records 50,630 -> 20; all 20 notifications retained. Logical 600 seconds compressed in time on isolated plain NATS; not public TLS/soak evidence. Earlier pre-sliced synthetic figures are superseded. Evidence is in sibling workspace implementation-artifacts, prefix slim-parent-*-repaired-final-20261016.

Four review layers completed serially using a reused non-implementing reviewer due thread capacity; they are not independent blind reviews. Source repair is complete. Production protocol comparison, user-operated daily MC restart, physical upgrade/TalkBack and prolonged observation remain separate deployment validation.
