# Current Turn Lists and Focus Shortcuts Design

## Goal

Keep the right-side lists focused on the user's current work: execution rows should only show the current question's process, long answer lists should start compact, and keyboard focus should be reachable from anywhere inside the window.

## Requirements

- When a new question is submitted, the visible execution process list is cleared before the new run emits process rows.
- Execution process rows for the active chat are scoped to the current turn. Older turn process rows may remain in persisted chat state, but the active execution list only renders rows belonging to the active turn.
- The answer list shows at most the newest 100 rendered rows by default.
- If the full answer list has more than 100 rows, a "更多" button appears below the current model row and above the answer rows.
- Pressing "更多" increases the visible answer row limit by 100. The list expands upward by showing earlier rows. The button disappears once all rows are visible.
- Chats with 100 or fewer rendered answer rows do not show the "更多" button.
- Starting a new question or switching chats resets the answer list visible limit to 100.
- Window-local shortcuts work whenever keyboard focus is inside the program window:
  - `Alt+F`: focus the answer list, or the execution list when the detail panel is in execution mode.
  - `Alt+D`: focus the input box.
  - `Alt+G`: focus the history chat list.
  - `Alt+B`: focus the visible note list. In notebook view, focus the notebook list. In note detail view, focus the note entry list.

## Design

The implementation stays in `main.py` and follows the existing wxPython list-box rendering pattern.

Answer rendering will be split into two stages. The existing `_render_answer_list` logic will build rows into an in-memory list of `(text, meta)` pairs, then render only the tail slice allowed by `self.answer_visible_row_limit`. Context usage and current model rows remain pinned at the top and are not counted against the 100 answer-row limit. A new `answer_more_button` lives under the title/model area and calls `_show_more_answer_rows`, which increases the limit and re-renders.

Execution rows will include a `turn_idx` on new execution entries. New submissions will call a small reset helper before appending the next turn so the visible list immediately clears. `_current_execution_steps` will filter active-chat rows to `active_turn_idx`; history chats still render their saved steps so old archives are not silently changed.

Shortcut handling will be centralized in `_on_char_hook`. A small helper will recognize Alt-letter combinations and call focus helpers for detail, input, history, and notes. These helpers will use the currently visible control and will not depend on which child control previously had focus.

## Testing

Unit tests will cover:

- Default answer rendering hides older rows when more than 100 rows exist and shows "更多".
- Clicking "更多" reveals another 100 rows and hides the button once all rows are visible.
- Submitting a new question resets the answer row limit.
- Execution rendering filters active-chat steps to the current turn and clears visible process rows on new submission.
- `Alt+F`, `Alt+D`, `Alt+G`, and `Alt+B` focus the intended controls.
