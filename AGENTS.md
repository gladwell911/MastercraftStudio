# Project Instructions

- This is a wxPython desktop app used with screen readers. Any UI-facing change must preserve keyboard focus stability and avoid unnecessary foreground refreshes while background work is running.
- For UI changes, run targeted accessibility/performance regression tests before completion. At minimum, include the relevant `tests/test_*ui_automation.py` test and any model-specific workflow tests touched by the change.
- Do not schedule UI-thread work, repaint list controls, change list selection/focus, or write app state from background polling when there is no visible state change. This is required to keep Tab and arrow-key navigation responsive with screen readers.
