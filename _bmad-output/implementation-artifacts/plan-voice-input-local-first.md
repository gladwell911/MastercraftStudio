---
title: '双 Ctrl 先采音提示，再后台连接 ASR'
type: 'bugfix'
ticket: ''
created: '2026-11-10'
status: 'built'
baseline_revision: '98016ccdb47f629888d8226900bbcc8272b0df1b'
route: 'full'
route_source: 'auto'
review: 'thorough'
review_source: 'auto'
lenses_ran: ['blind-hunter', 'edge-case-hunter', 'verification-gap', 'intent-alignment']
review_loop_iteration: 0
followup_review_recommended: true
context:
  - 'D:/code/sj/mc/_bmad-output/implementation-artifacts/plan-voice-input-startup-20261110.md'
  - 'D:/code/sj/mc/_bmad-output/implementation-artifacts/voice-input-startup-20261110/ARCHITECTURE-SPINE.md'
warnings: ['oversized']
deferred:
  - summary: >-
      Hard cleanup cutoff with actual aiohttp remains unverified.
    evidence: |-
      Hard cleanup deadline only failed with cancellation-suppressing fake. Actual aiohttp closes response and re-raises CancelledError; real transport reproduction required.
    location: >-
      realtime_asr.py:_close_resources
    severity: medium (unverified)
  - summary: >-
      Physical key-to-audible onset and real-cloud opening phrase remain unverified.
    evidence: |-
      Acoustic onset unverified: playback replaced; actual hardware ready P95 is 250ms but not audible onset. Actual physical hook/playback/cloud recognition were not tested.
    location: >-
      voice field acceptance
    severity: medium (unverified)
  - summary: >-
      Original hours-dependent mechanism and repaired 4-8h acceptance remain unverified.
    evidence: |-
      Hours-dependent issue is pre-existing; short hardware/100-cycle tests do not prove long-runtime outcome.
    location: >-
      voice long-runtime acceptance
    severity: medium (unverified)
---

<intent-contract>

## Intent

**Problem:** Double Ctrl delays microphone capture and its audible cue while the GUI synchronously waits for ASR startup; field samples measured 1.4/2.5 seconds waiting there.

**Approach:** Capture locally and announce readiness after microphone start plus the first buffered frame. Connect ASR concurrently; transmit buffered and subsequent audio in order, preserving the opening words.

## Boundaries & Constraints

**Always:** The two context documents are normative, including AD-1–AD-5, deadlines, state transitions and failure semantics. GUI commands never wait for devices, futures or joins. One audio worker owns device lifecycle; one ASR loop owns networking. Generation-bound callbacks are checked again when delivered to GUI. Use one FIFO: 640000 PCM bytes, at most one additional 6400-byte in-flight frame; coalesce wakeups. Overflow fails the entire recording without inserting partial text. Stop seals after callbacks quiesce, drains, and sends exactly one terminal packet; success requires these steps plus protocol final and no cancellation/failure. Preserve existing shortcut and text-mode behavior.

**Never:** Silently drop frames, play ready before actual capture, reset startup buffers, or let old tasks affect new sessions. No connection prewarming/reuse, provider changes, database work, packaging or daily-MC restart. Historical long-runtime/network root cause remains unproven.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Slow ASR | First PCM available; connect delayed | Immediate local cue; ordered PCM later | Connect deadline remains 10s |
| Early stop | Capturing, not connected | Stop cue; stop device; retain/seal buffer; connect/drain in background | No GUI wait or deadline reset |
| Cancel/close | Any phase, including pending GUI callbacks | Invalidate session; discard PCM; abort; no late insertion/cue | Idempotent bounded cleanup |
| Invalid capture/backpressure | No first frame, overflow, full FIFO | Stop and report one accessible error; no partial insertion | Device/network errors distinguished |
| Early final/failure | Before seal/drain/terminal success | Reject incomplete result | Atomic failure/success arbitration |

</intent-contract>

## Code Map

- `speech_input.py`: `VoiceInputController`, Ctrl detector, synchronous startup/stop/cancel; introduce per-session ownership, bounded PCM bridge and audio worker.
- `realtime_asr.py`: `RealtimeAsrClient.start/push_audio/_sender/_receiver/stop`; remove queue reset/silent drops; separate finish from abort; preserve protocol encoding/text extraction.
- `main.py`: controller construction, `_on_voice_state/_on_voice_error/_on_close`; guarded GUI events and shutdown. Keep global hook timing and unrelated realtime calls unchanged.
- `tests/test_voice_controller_unit.py`, `tests/test_realtime_asr_unit.py`, `tests/test_voice_input_integration.py`, `tests/test_voice_input_ui_automation.py`, `tests/test_voice_input_e2e.py`: extend existing fixtures; direct-result callback tests alone do not prove real startup.

## Tasks & Acceptance

**Execution:**
- [x] `tests/test_voice_controller_unit.py`, `tests/test_realtime_asr_unit.py` -- add controlled stream/connection gates and ordered PCM fixtures -- reproduce the blocked-local-ready defect without long sleeps.
- [x] `speech_input.py` -- implement generation/state lifecycle, audio worker, bounded shared FIFO, local-ready event, asynchronous stop/cancel/shutdown -- remove GUI blocking and isolate stale work.
- [x] `realtime_asr.py` -- implement concurrent connection, FIFO sender, seal/drain/final, explicit abort and deadlines -- preserve complete audio and guarantee terminal cleanup.
- [x] `main.py` -- validate queued events at execution, emit one readiness cue, expose useful errors, invoke shutdown -- preserve accessible interaction after races and close.
- [x] `tests/test_voice_input_integration.py`, `tests/test_voice_input_ui_automation.py`, `tests/test_voice_input_e2e.py` -- exercise matrix boundaries, duplicate keys, stale events and repeated lifecycle -- verify the user surface and resource convergence.

**Acceptance Criteria:**
- Given an isolated app with ASR delayed 5s, when double Ctrl starts speech immediately after its audible cue, then the cue precedes connection completion, the UI stays responsive, and inserted text retains the opening phrase.
- Given normal audio hardware, when at least 20 actual double-Ctrl starts are measured, then audible onset P95 is at most 500ms; API timestamps alone cannot establish audible onset.
- Given sessions ending via stop/cancel/error/close, when another session starts or queued callbacks arrive, then no previous audio/text/cue reaches that session or a destroyed window, and resources return to baseline.
- Given unchanged direct/optimize modes and left/right shortcuts, when their existing interactions run, then insertion, stop feedback and repeated identical utterances retain their intended behavior.

## Implementation Notes

- Implemented session-scoped generation/state arbitration, one persistent device worker and one ASR loop. GUI commands only accept/queue work; GUI delivery checks both window lifetime and session validity. Window shutdown invalidates voice events before the persistence-veto branch.
- A single thread-safe PCM FIFO enforces 640000 queued bytes plus at most one 6400-byte in-flight frame. First valid buffered PCM after stream.start triggers local readiness independently of connection. Wakeups coalesce; overflow marks irreversible failure under the FIFO lock.
- Stop closes/quiesces the device before seal, retains preconnection audio, drains in order, emits one empty negative-sequence terminal, and requires protocol final before atomic result acceptance. Cancel discards and aborts asynchronously. Connection/first-frame/drain/final/cleanup deadlines are implemented; cleanup errors are observable and reject success.
- Added gated device/transport fixtures and actual protocol decoding tests, cancellation/stale-event cases, 100-cycle resource convergence, deadline/backpressure/cleanup-error cases, and four wx message-pump/capture-to-editor tests. Existing direct callback tests remain surface-only evidence.
- Diagnostics are bounded in-memory per-generation monotonic metadata; they contain no audio/transcript/credentials. Field acoustic measurements and 4–8h runtime checks are still outstanding. Native device calls that never return remain isolated on their single owner thread; Python cannot forcibly reclaim a blocked driver.

## Plan Change Log

## Review Triage Log

### 2026-11-10 - Review pass
- verdicts: 22 findings - high 6, medium 6, low 1, false 3, maybe-false 6
- environment: Reviewer spawn hit agent thread limit; reused dog sequentially for four lenses. Four lenses are not four context-independent reviewers.
- findings:
  - `[high]` `[patch]` B1: Valid final can arrive before terminal send returns; gated send reproduced rejection. Wait for started-send settlement. Applied by director; the corresponding regression ran and passed in engineer verification.
  - `[maybe-false]` `[defer]` B2: Hard cleanup deadline only failed with cancellation-suppressing fake. Actual aiohttp closes response and re-raises CancelledError; real transport reproduction required.
  - `[low]` `[patch]` B3: Diagnostic snapshot precedes result/late GUI milestones. Retain bounded delivery-aware metadata. Applied by director; the corresponding regression ran and passed in engineer verification.
  - `[maybe-false]` `[defer]` B4: Acoustic onset unverified: playback replaced; actual hardware ready P95 is 250ms but not audible onset.
  - `[maybe-false]` `[defer]` B5: Hours-dependent issue is pre-existing; short hardware/100-cycle tests do not prove long-runtime outcome.
  - `[medium]` `[patch]` B6: Device/protocol fault fixtures omit lifecycle errors; add durable once-error/no-partial-result tests. Applied by director; the corresponding regression ran and passed in engineer verification.
  - `[medium]` `[patch]` B7: Optimize worker-to-GUI guard lacks coverage; gate normal/new/cancel/closed editor delivery. Applied by director; the corresponding regression ran and passed in engineer verification.
  - `[medium]` `[patch]` B8: Cleanup-error fake sets closed before throwing; add gated/unclosed close evidence without inventing resource release. Applied by director; the corresponding regression ran and passed in engineer verification.
  - `[false]` `[reject]` B9: Earlier Auto Run Result is planning history; workflow finalizes it. Step-04 rejects findings fixed only by editing the plan.
  - `[high]` `[patch]` E1: Same verified final/send root cause as B1. Applied by director; the corresponding regression ran and passed in engineer verification.
  - `[maybe-false]` `[defer]` E2: Same synthetic-only hard deadline reachability as B2; no speculative task isolation.
  - `[high]` `[patch]` E3: Repeated failure cancellation interrupted shielded cleanup: idle/network_done coexisted with open socket. Cancel once and gate completion on cleanup. Applied by director; the corresponding regression ran and passed in engineer verification.
  - `[high]` `[patch]` E4: Same E3 root cause violates resource-baseline claim; shared settlement fix. Applied by director; the corresponding regression ran and passed in engineer verification.
  - `[medium]` `[patch]` V1: Preverified gap: optimize guard deletion leaves existing tests green. Add gated GUI consumer coverage. Applied by director; the corresponding regression ran and passed in engineer verification.
  - `[medium]` `[patch]` V2: Preverified gap: swallowing device stop/close errors leaves current fixtures green. Add fault cases. Applied by director; the corresponding regression ran and passed in engineer verification.
  - `[medium]` `[patch]` V3: Preverified gap: 2s functional wait permits 800ms ready regression. Add controlled software bound, not acoustic assertion. Applied by director; the corresponding regression ran and passed in engineer verification.
  - `[high]` `[patch]` V4: Same B1/E1 race; delay terminal-send return in regression. Applied by director; the corresponding regression ran and passed in engineer verification.
  - `[maybe-false]` `[defer]` V5: Same B2/E2 synthetic-only cancellation resistance; actual aiohttp cancellation source checked.
  - `[high]` `[patch]` V6: Same E3/E4 repeated-cancellation race; gate cleanup regression. Applied by director; the corresponding regression ran and passed in engineer verification.
  - `[false]` `[reject]` I1: Diff does not prove procedure, but actual session ran renderer once, committed prior plans, then dog/director/engineer. No dispatch defect demonstrated.
  - `[false]` `[reject]` I2: Physical vs controlled evidence limits are explicitly disclosed; no false physical acceptance claim is made.
  - `[maybe-false]` `[defer]` I3: Physical/cloud/long-runtime outcomes unknown; retain field limitations with B4/B5.

## Design Notes

Full route: coordinated lifecycle/protocol/UI changes exceed 100 lines. Existing approved plan supplies implementation detail; do not redesign it. Stop-before-connect is finish, not cancellation. Cancellation invalidates immediately, but cleanup stays asynchronous. Enforce fixed lock order and atomic failure marking before scheduling error notification.

## Verification

Implementation self-check (2026-11-10): core command below: **30 passed**. Voice-related main-model regression selection: **47 passed, 982 deselected** (`tests/test_main_unit.py` plus the three core files, `-k 'voice or fifo or receiver or extract_text'`). Serial wx files: **15 passed, 26 failed**; all four new actual capture/protocol/message-pump cases passed. An isolated HEAD copy of the original two wx test files and original main/speech/ASR modules produced **11 passed and the identical 26 failures** (model choices, existing chat-arrow navigation, notes Alt-X); these unrelated failures were not changed. Exact baseline/current output is agent-owned temporary evidence under `C:/Users/gladwell/AppData/Local/Temp/mc-voice-baseline-6is0yu3m/`, retained for independent verification and containing no user data or credentials.

No real hardware/network transcription, acoustic onset/20-start P95, 0/3/5s field comparison, 4–8h soak, deployment, packaging, or daily-process restart was performed. Controlled event gates establish ordering and responsiveness, not physical/acoustic latency acceptance.

**Commands:**
- `.venv/Scripts/python.exe -m pytest tests/test_voice_controller_unit.py tests/test_realtime_asr_unit.py tests/test_voice_input_integration.py -q` -- expected: matrix, byte order, deadlines, single EOF and 100-cycle cleanup pass.
- `.venv/Scripts/python.exe -m pytest tests/test_voice_input_ui_automation.py tests/test_voice_input_e2e.py -q` -- expected: serial wx runs pass, fixtures dispose timers/workers/loops.
- `git diff --check` -- expected: clean.

**Field checks:** Use isolated data and an input sink; record per-generation monotonic milestones without credentials/audio/text. Compare 0/3/5s connection delays, verify opening words, audible onset using aligned loopback evidence, and GUI-response P95 ≤100ms. Repeat after 4–8h before claiming long-runtime acceptance; record unavailable evidence honestly. No deployment or daily-process manipulation is authorized by this verification plan.

## Auto Run Result

### Planning checkpoint

Template adaptation was ready-for-dev before implementation; product files were unchanged at that earlier checkpoint.

### Final result - 2026-11-10

Status: built. Local-first recording is implemented and independently verified at the software boundary. Microphone capture and first PCM now precede the ready cue; ASR connects concurrently, buffers in one bounded FIFO and sends all accepted PCM in order. GUI commands do not wait for audio devices or network Futures.

Files changed:
- `../../speech_input.py`: session generation, single audio owner, asynchronous lifecycle and bounded diagnostic metadata.
- `../../realtime_asr.py`: bounded FIFO, ordered sender, single terminal packet, final/send arbitration and cleanup settlement.
- `../../main.py`: GUI delivery/lifetime guards, accessible errors and voice shutdown.
- `../../tests/test_voice_controller_unit.py`, `../../tests/test_realtime_asr_unit.py`, `../../tests/test_voice_input_integration.py`: lifecycle, protocol, fault, deadline and concurrency regressions.
- `../../tests/test_voice_input_ui_automation.py`, `../../tests/test_voice_input_e2e.py`, `../../tests/test_main_unit.py`, `../../tests/voice_fakes.py`: gated capture/transport, GUI/editor and software-latency checks.
- This plan and `verify-voice-input-local-first.md`: review decisions, verification and evidence limits.

Review outcome: 22 individually logged findings. Seven patch groups applied: terminal-final race (B1/E1/V4), repeated cancellation/cleanup settlement (E3/E4/V6), delivery diagnostics (B3), exceptional device/protocol coverage (B6/V2), optimize GUI gate tests (B7/V1), unreclaimed-close observations (B8), software ready timing (V3). Entry verdict counts: 2 high, 4 medium, 1 low. Three unverified residual groups are recorded once in frontmatter. Rejected: B9 is planning history finalized here and a plan-only edit finding; I1 lacks any demonstrated procedural violation; I2 mistakes explicitly disclosed controlled-test limits for an unsupported completion claim.

Follow-up review recommended: true under the workflow rule for high concurrency patches. Specific unverified risk: actual cloud terminal-send/final timing and cancellation behavior has not been exercised; only gated protocol transports cover these repaired interleavings. Platform thread limit forced sequential reuse of dog across lenses, so reviewer context independence is reduced. This one build-auto iteration does not claim an independent follow-up pass.

Final independent verification:
- Core three files: 41 passed.
- `tests/test_main_unit.py -k voice`: 17 passed, 982 deselected.
- Serial wx related selection: 9 passed, 37 deselected.
- Matrix rows all have executed passing cases; both reproduced concurrency defects and queued result diagnostics ran explicitly.
- Original full wx run had 26 failures identical to the verified original-HEAD baseline; no unrelated failure is counted as fixed or passing. Baseline/current failure evidence is retained separately.
- Earlier actual Shure device metadata: 20-start ready-callback P95 250ms, GUI-response P95 15ms, 3/5s mock connection delay did not delay local ready. These measurements predate the review-only concurrency changes and are not presented as remeasured results.
- `git diff --check` passed. See `verify-voice-input-local-first.md` for commands, phase-specific evidence and cleanup.

Residual limits: no physical global-keyboard-to-acoustic-onset measurement, real cloud transcription/opening phrase, or 4-8h acceptance. Native device calls cannot be forcibly killed. Cancellation-resistant synthetic close exceeded its budget, but the equivalent actual-aiohttp failure is unverified and no speculative isolation was added. Daily MC was not stopped/restarted/replaced; no packaging, deployment or push.

Cleanup: all owned tests, devices, wx fixtures and worker/loop resources were closed. Retain the small baseline/current failure evidence directory under system Temp for the known 26 failures; it contains baseline source and failure lists, not user data. Generated review diff/cache may be removed after local commit because Git can reproduce them.

## Code Review

### 2026-11-10 — 新 dog 独立复审

Code review complete: 0 decision-needed, 1 patch, 5 defer, 2 rejected. 四名新context-free reviewer逐个执行；edge返回空数组，按workflow记为failed layer，因此不宣称完整clean review。完整逐项证据见 [新审查报告](review-voice-local-first-new-dog-20261110.md)。未改产品代码或计划状态。

- [x] [Review][Patch] V1：补控制器累计识别文本回归 [tests/test_voice_input_integration.py:56] — 原审查时每代单final不能检出拼接/首版保留；已由 `22284df21b428800384d32de74450a4980c93ad4` 补强，多interim再final、direct/optimize及两代同文各一次均覆盖，两类内存变异均被捕获。见 [V1局部计划](plan-v1-voice-cumulative-transcript-regression.md)。
- [x] [Review][Defer] B1/I2：设备错误后真实未释放可达性 [speech_input.py:265] — deferred: medium unverified；需实际driver失败及未释放证据。现有fake先释放再抛错不能证明该问题，sounddevice默认忽略错误码。
- [x] [Review][Defer] B3/I2：真实transport硬清理上限 [realtime_asr.py:_close_resources] — deferred: medium unverified；仅取消抵抗fake曾超预算，需实际aiohttp等效复现。
- [x] [Review][Defer] B4/I3：物理键到声学出声P95 — deferred: medium unverified；需20次对齐声学测量，ready回调不能替代。
- [x] [Review][Defer] B5/I3：真实云端慢连接句首 — deferred: medium unverified；固定文本fixture只证明字节与GUI边界，需真实识别。
- [x] [Review][Defer] B6/I3：4–8小时验收 — deferred: medium unverified；100周期/短硬件数据不能替代长稳。

Rejected:
- B2 false：规范规定已连接且seal后才开始12秒drain预算，现实现符合。
- I1 false：规范明确starting取消、recording未连接时stop保留，未发现意图偏离。

该次审查重新执行：核心41 passed；main voice 17 passed/982 deselected；wx定向9 passed/37 deselected；git diff --check通过。当时V1待下游修复，现已完成下述复验。

### 2026-11-10 — V1补强完成状态

新dog独立复审已落实原follow-up建议，其edge failed layer与blind数量不足局限保留，不宣称完整clean review。V1只改测试，president quick审查0项问题；engineer独立运行累计文本定向两模式2 passed（0.06s），核对默认fixture行为、真实receiver/controller链路和两类变异设计，并复用未变化代码的核心41/main17/wx9证据。原审查的5项未证实现场边界保持，原逐项历史裁决不覆盖。当前可接续快照见 [handoff](../../docs/handoff.md)。
