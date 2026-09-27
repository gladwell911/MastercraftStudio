---
status: built
---

# BMad Build Auto Result — Story 1.1

Status: built

## Auto Run Result

- The earlier ticket-resolution blocker was resolved by updating the two `bmad-build-auto` references to the installed `bmad-preview-ticketing/scripts/tickets.py`. `find 1.1` now resolves the story and its plan.
- Codex token events now use the latest `last` usage and the server-provided context window. The application menu opens a dedicated keyboard-readable information list for Codex/Kimi chats; Escape closes it and restores the previous control's focus.
- Worker events carry a stable turn index and context generation. The UI rejects stale token events and acknowledgements after clear or native thread replacement, while accepting a scoped early token event and late usage for the latest completed turn. Worker scope maps retain at most 256 recent identities.
- Final independent verification: 189 passed, 0 failed (138 related tests and 51 targeted main tests); `py_compile` and `git diff --check` passed. GUI coverage includes chat/thread/account identity switches, clear/direct thread replacement, early usage before worker acknowledgement, and two turns on one native thread. Existing context UI/E2E assertions were updated for the pre-existing time row; production time-row code was unchanged. A prior broad main suite run had 31 unrelated baseline failures across other features and one related fixture failure that was corrected.
- President and dog review findings were fixed and rechecked. The final dog review found no blocking issue. No follow-up review is recommended; the broad main suite still contains unrelated baseline failures.
