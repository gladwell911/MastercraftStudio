---
title: 'V1 cumulative voice transcript regression coverage'
type: 'chore'
ticket: ''
created: '2026-11-10'
status: 'built'
baseline_revision: '71e05f5d83ae27bf6166914f58421c352beafec5'
route: 'oneshot'
route_source: 'auto'
review: 'quick'
review_source: 'auto'
lenses_ran: ['quick']
review_loop_iteration: 0
followup_review_recommended: false
context: []
warnings: []
deferred: []
---

<intent-contract>

## Intent

**Problem:** New-dog review V1 identifies missing controller integration coverage for multiple cumulative interim transcripts followed by a distinct final. Receiver-only callback tests do not detect controller concatenation or first-version retention.

**Approach:** Add opt-in interim protocol responses to `tests/voice_fakes.py`, preserving its default final-only behavior. Extend `test_latest_text_and_identical_utterances_in_distinct_generations` so each generation receives two different cumulative interims and one different complete final through the real receiver/controller/convergence path. Assert exactly one complete final per generation, including two generations with identical final text, for direct and optimize modes. Verify both erroneous controller implementations using isolated in-memory method replacement, never changing product files.

</intent-contract>

## Implementation Notes

Oneshot: fewer than 100 changed lines across two test files; the review supplies the complete minimal approach. No product logic changes, physical/cloud/acoustic measurements, packaging, daily-process changes, or unrelated regression repairs are in scope.

- Added opt-in `interim_texts` protocol responses; default remains an empty list/final only. The integration observer forwards every packet to the real controller method, then records delivery, ensuring two interims were processed before stop and the distinct final before convergence.
- Core three-file run: 41 passed (1.22s). Serial wx selection: 9 passed, 37 deselected (2.54s).
- Isolated subprocess method replacements in memory (no product-file edits): append mutation produced `openingopening phraseopening phrase opening phrase`; first-only produced `opening`. Each mutation failed both direct and optimize cases at the final result equality assertion (2 failed each, no teardown errors). Processes restored the original method in finally and exited. No mutation files or copied worktrees were created.

## Plan Change Log

## Review Triage Log

### 2026-11-10 — Review pass
- verdicts: 0 findings — high 0, medium 0, low 0, false 0, maybe-false 0
- findings: president quick reviewer returned no unmet acceptance criteria, broken project rules, or confirmed bugs. No patches, deferrals, or rejections.

## Verification

- `.venv/Scripts/python.exe -m pytest tests/test_voice_controller_unit.py tests/test_realtime_asr_unit.py tests/test_voice_input_integration.py -q` — core passes.
- In-memory concatenation and first-only replacements of `_stream_text` — extended targeted case fails in both modes; original restored and passes.
- `.venv/Scripts/python.exe -m pytest tests/test_voice_input_ui_automation.py tests/test_voice_input_e2e.py -k "local_ready or queued_ready or optimize_worker or software_ready or actual_capture" -q` — shared fixture default behavior remains compatible; serial wx selection only.
- `git diff --check` — clean.

## Auto Run Result

Status: built. V1 is covered by opt-in encoded interim responses and the expanded two-generation controller integration case; product logic is unchanged. Changed files: `tests/voice_fakes.py` adds default-empty interim responses, `tests/test_voice_input_integration.py` checks all updates plus exactly one complete result per generation in both modes, and this local plan records execution evidence.

Review: quick/president, zero findings; patched counts high 0, medium 0, low 0; deferred 0, rejected 0. Follow-up review recommendation: false.

Verification: core three files 41 passed; shared-fixture serial wx selection 9 passed, 37 deselected; each in-memory append/first-only mutation caused 2 expected final-equality failures, and the unchanged original then passed both targeted cases (2 passed). `git diff --check` passed. The review diff was the only task-created temporary file and was removed after review; mutation subprocesses and test workers/loops exited, with no on-disk mutation or copied worktree. Rendered workflow cache remains in the existing ignored BMAD cache.

Residual boundaries: only the documented software regression gap is addressed. Existing acoustic, real cloud recognition, and long-runtime acceptance limitations are unchanged; no packaging, restart, deployment or push was performed.
