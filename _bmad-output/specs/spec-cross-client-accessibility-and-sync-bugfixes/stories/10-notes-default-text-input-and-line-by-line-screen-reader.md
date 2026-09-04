---
title: 'Story 10: Notes Default Text Input and Line-by-Line Screen Reader'
type: 'bugfix'
created: '2026-09-04'
status: 'done'
baseline_revision: 'ebae52f0f92991bf05d6145b73fc1b5f2360729b'
review_loop_iteration: 0
followup_review_recommended: true
context:
  - 'D:\\code\\sj\\rc\\AGENTS.md'
warnings: []
deferred:
  - summary: >-
      Four legacy NotesPage sync fixtures cannot run because they instantiate NotesSyncService without its required NotesSyncClient.
    evidence: |-
      The full notes_page_test.dart run fails at NotesSyncService's pre-existing null-client constructor guard, introduced before this Story 10 baseline; the failures are in legacy transport-based sync fixtures and do not exercise the changed note-detail surface.
    location: >-
      D:\code\sj\rc\test\notes_page_test.dart:726,757,835,1402; D:\code\sj\rc\lib\notes_sync_service.dart:94
    severity: medium
---

<intent-contract>

## Intent

**Problem:** Opening a mobile note detail starts in voice input mode, so users cannot immediately type. Multi-line note entries visually preserve line breaks but expose the whole entry as one screen-reader focus target, preventing line-by-line reading.

**Approach:** Make text input the initial note-detail mode while retaining the explicit, accessible switch to voice input. Project each non-empty preserved text line as an individually reachable semantic/focus target in document order, without changing note content or editing/deletion behavior.

## Boundaries & Constraints

**Always:** On the first rendered frame of `NotesThreadPage`, show the bottom text editor and the lower-left control labelled for switching to voice input; autofocus may occur after that rendered frame. Preserve the original visual line breaks, skip blank lines as independent reading targets, and expose every non-empty line once in source order to screen readers and keyboard focus. Retain voice entry, text save, entry tap/detail, long-press delete, scrolling, note serialization, Chinese labels, and existing voice permissions/feedback. Focus navigation must remain stable and not create a route, edit, save, or announcement merely by reading lines.

**Block If:** Satisfying line-level focus requires changing the shared note content format, note synchronization/protocol, desktop `mc` application, or a product decision about presenting blank lines as accessible content.

**Never:** Do not modify completed Story 1, Story 3, Story 4, Story 5, Story 6, Story 7, Story 8, or Story 9 specifications. Do not replace voice input, remove the user-controlled mode switch, alter existing note text/newlines, merge lines into a single semantic label, or add desktop changes.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|---------------|----------------------------|----------------|
| Default detail mode | A user opens an existing or newly created note detail | The first rendered bottom bar is text input, with an editable field and a lower-left `switch to voice input` control | Focus is requested only while mounted; an unavailable focus target leaves the rendered text mode intact |
| Multi-line entry | An entry contains text lines separated by LF or CRLF, including blank lines | Visual content retains the source line breaks; each non-empty line is an independent semantic and keyboard focus target in original order | Blank lines remain visual spacing but produce no empty/duplicate focus target |
| Voice-mode return | The user switches from default text mode to voice mode, then back | Existing voice press-and-hold flow remains available; returning to text mode restores the editor and focus | Voice errors remain on the existing announcement path and do not change stored note text |

</intent-contract>

## Code Map

- `D:\code\sj\rc\lib\notes_thread_page.dart:32-95,273-290` -- owns the note-detail input-mode default and deferred editor focus; initialize text mode and retain the existing switch/voice lifecycle.
- `D:\code\sj\rc\lib\notes_thread_page.dart:294-389` -- currently gives an entire `NoteEntry` one focus/semantic label while `_buildEntryLines` only draws text; refactor this presentation into ordered non-empty line targets without changing entry actions.
- `D:\code\sj\rc\lib\notes_thread_page.dart:391-467` -- renders the mutually exclusive text and voice bottom bars; text branch already contains the needed editor and voice-switch icon.
- `D:\code\sj\rc\lib\notes_models.dart:127-153` -- `noteEntriesFromContent` and `encodeNoteEntries` retain entry content; keep this storage/synchronization representation read-only for this UI accessibility change.
- `D:\code\sj\rc\test\notes_page_test.dart:885-953,1047-1085` -- existing note-detail semantic, keyboard-entry navigation, and text-mode-focus regressions; update/add focused default-mode and per-line semantics/focus-order coverage here.
- `D:\code\sj\mc` -- read-only related desktop project; Story 10 implements CAP-10/CAP-11 only in the Flutter mobile client.

## Tasks & Acceptance

**Execution:**
- `D:\code\sj\rc\lib\notes_thread_page.dart` -- default note details to the existing text composer and adapt entry rendering/focus ownership so non-empty source lines have stable ordered semantic and keyboard targets while entry actions remain reachable.
- `D:\code\sj\rc\test\notes_page_test.dart` -- replace whole-entry semantic expectations and add widget regressions for first-frame text mode, voice-switch availability, preserved LF/CRLF layout, blank-line exclusion, per-line semantic labels, and ordered focus traversal.

**Acceptance Criteria:**
- Given a user enters a mobile note detail, when its first frame is rendered, then a text editor is visible at the bottom and the lower-left accessible action switches to voice input.
- Given a note entry contains multiple text lines, when a screen reader or keyboard traverses the entry, then each non-empty source line is reached once in original order and read independently while the visual line breaks remain unchanged.
- Given a note has blank lines or CRLF line endings, when its detail is rendered and traversed, then blanks do not create empty targets and the remaining non-empty lines preserve their original order.

## Spec Change Log

## Review Triage Log

### 2026-09-04 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 5: (medium 4, low 1)
- defer: 1: (medium 1)
- reject: 11: (low 11)
- addressed_findings:
  - `[medium]` `[patch]` Added ArrowUp traversal across an entry boundary so the new flattened line focus order is verified in both directions.
  - `[medium]` `[patch]` Verified line-level semantic tap and long-press actions open and delete their owning entry after the parent InkWell became semantics-excluded.
  - `[medium]` `[patch]` Updated downstream notes end-to-end and acceptance regressions from the removed whole-entry/voice-default contract to per-line semantics and default text mode.
  - `[medium]` `[patch]` Guarded first-frame editor focus with the current modal route and added a replacement-route focus regression.
  - `[low]` `[patch]` Covered whitespace-only and tab-only source lines so they remain visual-only and cannot become empty accessibility targets.

## Design Notes

The existing entry-level focus owns keyboard arrow navigation and the entry-level tap/long-press actions. Line-level reading targets must not accidentally make the outer entry an additional duplicate semantic target or break those actions. The implementation should keep a single ordered source of truth for line splitting and stable focus-node cleanup when entries change.

## Verification

**Commands:**
- `C:\\src\\flutter\\bin\\flutter.bat test test\\notes_page_test.dart` -- expected: note-detail default input, line-by-line accessibility, entry interaction, and notes regressions pass.
- `C:\\src\\flutter\\bin\\flutter.bat analyze lib\\notes_thread_page.dart test\\notes_page_test.dart` -- expected: no diagnostics in changed Flutter files.
- `git diff --check` -- expected: no whitespace errors.

## Auto Run Result

**Summary:** Note details now open directly in text-input mode, with the existing lower-left switch to voice input. Multi-line entries retain their visual line breaks while each non-empty source line is exposed once, in source order, as an independent semantic and keyboard-focus target.

**Files changed:**
- `D:\code\sj\rc\lib\notes_thread_page.dart` -- defaults to the text composer, protects deferred focus from an inactive route, and replaces entry-wide accessibility targets with source-line targets.
- `D:\code\sj\rc\test\notes_page_test.dart` -- covers first-frame mode, voice switching, route-focus safety, LF/CRLF/whitespace semantics, bidirectional keyboard order, and line semantic actions.
- `D:\code\sj\rc\test\notes_end_to_end_test.dart` -- updates end-to-end expectations to independent accessible note lines.
- `D:\code\sj\rc\integration_test\notes_acceptance_test.dart` -- updates acceptance fixtures for default text mode and per-line semantics.
- This Story 10 spec -- records the plan, review triage, deferred legacy fixture issue, and verification evidence.

**Review findings:** Applied 5 patches (medium 4, low 1); deferred 1 medium pre-existing fixture issue; rejected 11 low-confidence, unsupported, or out-of-scope findings. Follow-up review recommendation: `true` (`3 × 4 + 1 = 13`).

**Verification:**
- `C:\\src\\flutter\\bin\\flutter.bat test test\\notes_page_test.dart --name "^(note detail first frame shows text entry and voice switch|note detail exposes non-empty lines as ordered semantics targets|note detail arrow navigation follows non-empty source lines in order|note detail line semantics expose entry tap and long press|note detail entry tap opens read only detail with paragraphs|note detail list long press deletes the selected entry|note detail defaults to text input and focuses the editor|note detail saves and announces the direct voice transcript immediately|replaced note detail cannot steal focus after its first frame)$"` -- passed, 9 tests.
- `C:\\src\\flutter\\bin\\flutter.bat test test\\notes_end_to_end_test.dart` -- passed, 1 test.
- `C:\\src\\flutter\\bin\\flutter.bat analyze lib\\notes_thread_page.dart test\\notes_page_test.dart` -- passed with no diagnostics.
- Expanded analysis including `test\\notes_end_to_end_test.dart` and `integration_test\\notes_acceptance_test.dart` reported only the pre-existing unused `_NotesEchoTransport` warning at `integration_test\\notes_acceptance_test.dart:147`.
- `C:\\src\\flutter\\bin\\flutter.bat test test\\notes_page_test.dart` -- Story 10 tests passed, but four unrelated legacy transport-based sync fixtures failed at the baseline null-client guard; recorded above as deferred.
- `git diff --check` -- passed.

**Residual risks:** Flutter widget tests prove separate semantic nodes and keyboard order, but final physical TalkBack traversal/announcements should be confirmed on a device. The acceptance integration suite could not run in this environment because no Android device was connected and this project has no Windows desktop target.
