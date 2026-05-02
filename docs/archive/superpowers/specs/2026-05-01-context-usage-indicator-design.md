# Context Usage Indicator Design

## Goal

The answer list should always show a fixed first row with the current chat's context usage. The row should refresh after each answer completes and should work for every model path: Codex, ClaudeCode, OpenClaw, and regular models from the model combo box.

## User-Facing Text

Use one compact, screen-reader-friendly sentence:

```text
上下文：12K/272K，4.4%已用
```

Formatting rules:

- If used tokens are below 1000, show `小于1K`.
- If used tokens are 1000 or higher, round to the nearest integer K.
- Always show the context window as integer K when known.
- Do not show decimal places in K values.
- Keep percentage to one decimal place.
- Add `约` only when the value is estimated rather than reported by the model provider or CLI.

Examples:

```text
上下文：小于1K/272K，0.3%已用
上下文：12K/272K，4.4%已用
上下文：113K/272K，41.6%已用
上下文：约 12K/128K，9.7%已用
上下文：12K，窗口未知
上下文：刷新中
```

## Accuracy Policy

Codex, ClaudeCode, and OpenClaw should use exact usage reported by their own CLI/API whenever available. Regular models should use API usage when available and local estimation only as a fallback.

The UI should not hide the difference between exact and estimated values. Exact CLI/API values omit `约`; estimated values include it.

## Data Sources

Codex:

- Prefer `token_count` events from Codex app-server notifications if they are emitted.
- Extract `total_token_usage.total_tokens` as used tokens.
- Extract `model_context_window` as the context window.
- If app-server does not forward token events, read the matching Codex session JSONL and use the newest `token_count` event for the active Codex thread/turn.

ClaudeCode:

- Parse `claude --print --output-format=stream-json --verbose` output.
- Assistant messages include `message.usage.input_tokens` and `message.usage.output_tokens`.
- Final result includes `modelUsage.<model>.contextWindow`, `inputTokens`, `outputTokens`, `cacheReadInputTokens`, and `cacheCreationInputTokens`.
- Use the actual model key from `modelUsage` so a ClaudeCode run that chooses a different model reports that model's real context window.

OpenClaw:

- Run or reuse data equivalent to `openclaw sessions --json`.
- Match the current app chat's `active_openclaw_session_id` to the returned `sessionId`.
- Use `totalTokens` as used tokens and `contextTokens` as the context window.
- Treat `totalTokensFresh` as a freshness flag. If it is false, keep the row but mark the source as stale in internal data.

Regular models:

- Extend streaming API parsing to capture provider usage if the provider returns it.
- If no usage is returned, estimate from the actual message transcript sent by `ChatClient._build_messages()`.
- Estimated values must render with `约`.

## Unified Data Model

Normalize every source into one structure before rendering:

```python
{
    "used_tokens": 113260,
    "context_window": 272000,
    "percent_used": 41.6,
    "source": "openclaw",
    "exact": True,
    "fresh": True,
    "model": "gpt-5.4",
    "updated_at": 1710000000.0,
}
```

Store the latest value on the current chat state:

```python
self._current_chat_state["context_usage"] = usage
```

Archived chats should persist the same key so history view can show the last known value immediately. Rendering should tolerate missing or partial data.

## Answer List Integration

`_render_answer_list()` should insert the context row before any conversation rows:

```python
self.answer_list.Append(label)
self.answer_meta.append(("context_usage", -1, label, ""))
```

The row is display-only:

- It is not written to `active_session_turns`.
- It is not included in chat transcript construction.
- It is ignored by answer activation, attachment opening, and latest-answer focusing.
- It remains visible when there are no turns, followed by the existing `暂无对话内容` row.

## Refresh Flow

Refresh context usage after each answer completes:

- In `_on_done()` for regular models, Codex, and ClaudeCode.
- After OpenClaw sync merges an assistant event into the active chat.
- After loading or switching chats, using persisted usage first and refreshing from exact sources when possible.
- After changing the model, because the same used-token count may map to a different context window.
- After new chat creation, clear the usage and render `上下文：刷新中` or a zero/unknown state until the first measurement exists.

Refresh should not block UI rendering. Slow exact lookups, especially OpenClaw `sessions --json` and Codex JSONL fallback reads, should run outside the UI thread and publish the normalized result back with the existing safe UI scheduling helpers.

## Failure Handling

If exact source lookup fails:

- Keep the previous known usage for the chat if present.
- If no previous value exists, fall back to local estimation for regular models.
- For CLI models, do not fabricate exact values. Use `上下文：刷新中` while a lookup is pending, or `上下文：窗口未知` if used tokens are known but window size is not.

Errors should not be shown as modal dialogs. They can be recorded in the usage structure for diagnostics.

## Testing

Add unit tests for:

- Formatting: `小于1K`, integer K rounding, one-decimal percentage, estimated `约`, unknown window.
- `_render_answer_list()` always inserts the context row first and keeps `暂无对话内容` below it for empty chats.
- `_focus_latest_answer()` ignores `context_usage` rows.
- Codex token events normalize to exact usage and use `model_context_window`.
- ClaudeCode stream JSON normalizes `modelUsage` and actual model context window.
- OpenClaw `sessions --json` matching by `sessionId` normalizes `totalTokens/contextTokens`.
- Regular model fallback renders estimated usage with `约`.

