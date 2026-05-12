# Project Instructions

- This is a wxPython desktop app used with screen readers. Any UI-facing change must preserve keyboard focus stability and avoid unnecessary foreground refreshes while background work is running.
- For UI changes, run targeted accessibility/performance regression tests before completion. At minimum, include the relevant `tests/test_*ui_automation.py` test and any model-specific workflow tests touched by the change.
- Do not schedule UI-thread work, repaint list controls, change list selection/focus, or write app state from background polling when there is no visible state change. This is required to keep Tab and arrow-key navigation responsive with screen readers.


<claude-mem-context>
# Memory Context

# claude-mem status

This project has no memory yet. The current session will seed it; subsequent sessions will receive auto-injected context for relevant past work.

Memory injection starts on your second session in a project.

`/learn-codebase` is available if the user wants to front-load the entire repo into memory in a single pass (~5 minutes on a typical repo, optional). Otherwise memory builds passively as work happens.

Live activity: http://localhost:37777
How it works: `/how-it-works`

This message disappears once the first observation lands.
</claude-mem-context>