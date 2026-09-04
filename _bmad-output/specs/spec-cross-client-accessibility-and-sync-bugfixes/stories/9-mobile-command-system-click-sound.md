---
title: 'Story 9: Mobile Command System Click Sound'
type: 'bugfix'
created: '2026-09-04'
status: 'done'
baseline_revision: '85833817a166f406c10f871f8f27abb9bca99081'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - 'D:\\code\\sj\\rc\\AGENTS.md'
warnings: []
deferred: []
---

<intent-contract>

## Intent

**Problem:** Selecting a saved command on the mobile command page sends the command without the requested platform-standard click feedback. The missing cue makes a successful command activation less perceptible to mobile and screen-reader users.

**Approach:** Play Flutter's platform `SystemSoundType.click` exactly once at the shared valid-command activation boundary, then retain the existing command-send and navigation behavior.

## Boundaries & Constraints

**Always:** Apply only to an enabled saved-command activation on the mobile `CommonCommandsPage`. Use the platform standard click sound, not a custom asset or haptic cue. Both ordinary touch and the exposed screen-reader semantic tap must take the same single feedback path, invoke the existing `onSendCommand` with the unchanged command content, and preserve the current page-close behavior after a successful send. A sound-platform failure must not prevent the command send.

**Block If:** Meeting the requirement requires a desktop change, a protocol/message-content change, a new audio asset, or a product decision about feedback for mutation controls rather than command activation.

**Never:** Do not play a second sound from a wrapper or gesture handler, add vibration, make unavailable/mutating commands actionable, change common-command storage/mutation behavior, or modify completed Story 1, Story 3, Story 4, Story 5, Story 6, Story 7, or Story 8 specs.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|---------------|---------------------------|----------------|
| Valid command touch | A loaded command is enabled and the user taps its list item | One `SystemSoundType.click` request occurs; the original command content is sent and the page closes after completion | A sound failure is isolated so the send still runs |
| Valid semantic activation | A screen reader invokes the command's semantic tap | The same one-click-sound and one-send path runs, with no duplicate cue | The semantic action remains truthful and usable |
| Unavailable command | The page is mutating and the command action is null | No sound and no command send occur | Existing disabled semantic state is retained |

</intent-contract>

## Code Map

- `D:\code\sj\rc\lib\common_commands_page.dart:191-197` -- `_handleSend` is the single async boundary called by both the outer command semantics and the `ListTile`; add the guarded platform click before forwarding to `onSendCommand` and popping.
- `D:\code\sj\rc\lib\common_commands_page.dart:536-556` -- each rendered command has both touch and semantic handlers that already delegate to `_handleSend`; retain their enabled-state gate to avoid duplicate feedback or feedback for unavailable commands.
- `D:\code\sj\rc\lib\main.dart:5239-5254` -- production `ChatPage` wires `onSendCommand` directly to `sendMessage(command.content)`; this is read-only evidence that the command content and route ownership must stay unchanged.
- `D:\code\sj\rc\test\common_commands_page_test.dart:1239-1318` -- existing ChatPage-to-command-page regression fixture and fake command service; extend it with a mocked platform channel to assert the standard-click call and unchanged outgoing content.
- `D:\code\sj\rc\test\chat_list_page_test.dart:157-221` -- established test pattern for capturing `SystemChannels.platform` calls and asserting exactly one `SystemSound.play` with `SystemSoundType.click`.

## Tasks & Acceptance

**Execution:**
- `D:\code\sj\rc\lib\common_commands_page.dart` -- add a best-effort platform standard click at the common valid-command activation boundary, while preserving the existing send/pop sequence and disabled-action guards.
- `D:\code\sj\rc\test\common_commands_page_test.dart` -- add widget regressions for one touch activation and one semantic activation, each asserting exactly one standard click request and the unchanged command dispatch; assert unavailable command controls emit neither feedback nor a send.

**Acceptance Criteria:**
- Given an enabled saved command on the mobile command page, when a user activates it through touch or screen-reader semantics, then the platform receives exactly one `SystemSound.play` request for `SystemSoundType.click` and the original command is sent exactly once.
- Given platform click playback reports an error, when the user activates an enabled command, then the original command send still proceeds according to its existing behavior.
- Given a command is unavailable while a mutation is in progress, when its non-actionable surface is reached, then no click sound or command send is produced.

## Design Notes

The shared `_handleSend` method is the only activation boundary needed: both visible touch and accessibility semantic actions already call it. Keeping feedback there prevents separate gesture and semantics branches from producing duplicate sounds.

## Verification

**Commands:**
- `C:\\src\\flutter\\bin\\flutter.bat test test/common_commands_page_test.dart` -- expected: command-page semantics, dispatch, and new platform-click regressions pass.
- `C:\\src\\flutter\\bin\\flutter.bat analyze lib/common_commands_page.dart test/common_commands_page_test.dart` -- expected: no analyzer diagnostics in changed files.
- `git diff --check` -- expected: no whitespace errors.

## Spec Change Log

## Review Triage Log

### 2026-09-04 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 1 (medium 1)
- defer: 0
- reject: 15 (low 15)
- addressed_findings:
  - `[medium]` `[patch]` Made platform-click feedback non-blocking and added a delayed-platform-call regression, so a stalled sound request cannot delay the original command dispatch.

## Auto Run Result

**Summary:** Added a best-effort platform-standard click sound to each enabled mobile saved-command activation. The command dispatch and successful page-close behavior remain unchanged; the sound request is non-blocking and its failure is isolated from sending.

**Files changed:**
- `D:\code\sj\rc\lib\common_commands_page.dart` -- invokes one non-blocking `SystemSoundType.click` from the shared command activation boundary and disables duplicate ListTile feedback.
- `D:\code\sj\rc\test\common_commands_page_test.dart` -- covers touch and semantic activation, platform sound failure and delay, unavailable commands, and the ChatPage production command path.
- This Story 9 spec -- records the implementation plan, review triage, and verification evidence.

**Review findings:** Applied 1 medium patch; deferred 0; rejected 15 low-confidence, duplicate, already-covered, or behavior-changing suggestions. Follow-up review recommendation: `false` (score `3 × 1 = 3`).

**Verification:**
- `C:\\src\\flutter\\bin\\flutter.bat test test\\common_commands_page_test.dart` -- passed, 21 tests.
- `C:\\src\\flutter\\bin\\flutter.bat analyze lib\\common_commands_page.dart test\\common_commands_page_test.dart` -- passed with no diagnostics.
- `git diff --check` -- passed (Git emitted only CRLF conversion notices).

**Matrix audit:** The touch row is covered by `common command touch plays one click and sends once`; the semantic row by `common command semantic tap plays one click and sends once`; the unavailable row by `unavailable common command emits no click or command send`. All three ran and passed. `common command still sends when platform click fails` and `common command send does not wait for platform click` additionally cover the sound-failure isolation required by the intent contract.

**Residual risks:** Flutter widget tests verify the single platform click request, not the operating system's physical audible playback. Final real-device TalkBack and audio confirmation remains a manual acceptance check.
