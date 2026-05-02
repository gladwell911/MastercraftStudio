# Context Usage Indicator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a fixed top row to the answer list showing the current chat's context usage, using exact CLI/API usage for Codex, ClaudeCode, and OpenClaw when available, and estimated usage for regular models when exact usage is unavailable.

**Architecture:** Create a small `context_usage.py` module that owns formatting, normalization, and fallback estimation. Keep UI integration in `main.py` by inserting a synthetic `answer_meta` row and storing normalized usage on chat state. Extend `codex_client.py`, `claudecode_client.py`, `openclaw_client.py`, and `chat_client.py` only enough to expose usage data through existing call paths.

**Tech Stack:** Python 3.11, wxPython `ListBox`, PyInstaller-safe subprocess calls, pytest.

---

## File Map

- Create `context_usage.py`: normalized usage structure, label formatting, model fallback windows, rough transcript estimation.
- Modify `main.py`: render the fixed context row, persist usage on chat state, refresh usage after completions and model/chat changes.
- Modify `chat_client.py`: collect exact streaming usage when provider returns it and expose `last_context_usage`.
- Modify `claudecode_client.py`: parse `stream-json --verbose` usage and expose `last_context_usage`.
- Modify `codex_client.py`: normalize app-server token count events into `CodexEvent`.
- Modify `openclaw_client.py`: read OpenClaw session usage from `sessions --json` output or an injected JSON payload in tests.
- Modify `tests/test_main_unit.py`: answer list row, focus behavior, refresh calls, normal model fallback.
- Modify `tests/test_chat_client_unit.py`: regular model usage capture and fallback message building.
- Modify `tests/test_claudecode_client_unit.py`: ClaudeCode `modelUsage` parsing.
- Modify `tests/test_codex_client_unit.py`: Codex token count event parsing.
- Modify `tests/test_openclaw_client_unit.py`: OpenClaw session usage parsing.

---

### Task 1: Context Usage Formatting Helpers

**Files:**
- Create: `context_usage.py`
- Test: `tests/test_context_usage_unit.py`

- [ ] **Step 1: Write failing formatting tests**

Create `tests/test_context_usage_unit.py` with:

```python
from context_usage import (
    ContextUsage,
    format_context_usage_label,
    format_token_k,
    normalize_context_usage,
)


def test_format_token_k_uses_less_than_one_k_for_small_values():
    assert format_token_k(0) == "小于1K"
    assert format_token_k(999) == "小于1K"


def test_format_token_k_rounds_to_integer_k():
    assert format_token_k(1000) == "1K"
    assert format_token_k(12400) == "12K"
    assert format_token_k(12600) == "13K"


def test_exact_context_label_with_window_and_percent():
    usage = ContextUsage(
        used_tokens=113260,
        context_window=272000,
        source="openclaw",
        exact=True,
        fresh=True,
        model="gpt-5.4",
        updated_at=1.0,
    )

    assert format_context_usage_label(usage) == "上下文：113K/272K，41.6%已用"


def test_estimated_context_label_adds_approximate_prefix():
    usage = ContextUsage(
        used_tokens=12400,
        context_window=128000,
        source="estimated",
        exact=False,
        fresh=True,
        model="openai/gpt-5.2",
        updated_at=1.0,
    )

    assert format_context_usage_label(usage) == "上下文：约 12K/128K，9.7%已用"


def test_unknown_window_label_omits_percent():
    usage = ContextUsage(
        used_tokens=12400,
        context_window=0,
        source="codex",
        exact=True,
        fresh=True,
        model="gpt-5.4",
        updated_at=1.0,
    )

    assert format_context_usage_label(usage) == "上下文：12K，窗口未知"


def test_missing_usage_label_is_refreshing():
    assert format_context_usage_label(None) == "上下文：刷新中"


def test_normalize_context_usage_computes_percent_and_bounds_values():
    usage = normalize_context_usage(
        used_tokens="113260",
        context_window="272000",
        source="openclaw",
        exact=True,
        fresh=True,
        model="gpt-5.4",
        updated_at=1.0,
    )

    assert usage.used_tokens == 113260
    assert usage.context_window == 272000
    assert usage.percent_used == 41.6
```

- [ ] **Step 2: Run the failing test**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_context_usage_unit.py -v
```

Expected: fails because `context_usage` does not exist.

- [ ] **Step 3: Implement `context_usage.py`**

Create `context_usage.py`:

```python
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


DEFAULT_CONTEXT_WINDOW = 128000

MODEL_CONTEXT_WINDOWS = {
    "codex/main": 258400,
    "claudecode/default": 200000,
    "openclaw/main": 272000,
    "openai/gpt-5.2": 128000,
    "openai/gpt-5.2-chat": 128000,
    "anthropic/claude-sonnet-4.6": 200000,
    "anthropic/claude-opus-4.6": 200000,
    "anthropic/claude-opus-4.5": 200000,
    "google/gemini-3.1-pro-preview": 1000000,
}


@dataclass
class ContextUsage:
    used_tokens: int
    context_window: int
    source: str
    exact: bool
    fresh: bool
    model: str
    updated_at: float
    percent_used: float = 0.0
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "used_tokens": self.used_tokens,
            "context_window": self.context_window,
            "percent_used": self.percent_used,
            "source": self.source,
            "exact": self.exact,
            "fresh": self.fresh,
            "model": self.model,
            "updated_at": self.updated_at,
            "error": self.error,
        }


def _int_value(value: Any, default: int = 0) -> int:
    try:
        return max(int(value or 0), 0)
    except Exception:
        return default


def _float_value(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or 0.0)
    except Exception:
        return default


def normalize_context_usage(
    *,
    used_tokens: Any,
    context_window: Any = 0,
    source: str,
    exact: bool,
    fresh: bool = True,
    model: str = "",
    updated_at: float | None = None,
    error: str = "",
) -> ContextUsage:
    used = _int_value(used_tokens)
    window = _int_value(context_window)
    percent = round((used / window * 100.0), 1) if window > 0 else 0.0
    return ContextUsage(
        used_tokens=used,
        context_window=window,
        percent_used=percent,
        source=str(source or "").strip(),
        exact=bool(exact),
        fresh=bool(fresh),
        model=str(model or "").strip(),
        updated_at=_float_value(updated_at, time.time()),
        error=str(error or ""),
    )


def context_usage_from_dict(value: dict | None) -> ContextUsage | None:
    if not isinstance(value, dict):
        return None
    return normalize_context_usage(
        used_tokens=value.get("used_tokens"),
        context_window=value.get("context_window"),
        source=str(value.get("source") or ""),
        exact=bool(value.get("exact")),
        fresh=bool(value.get("fresh", True)),
        model=str(value.get("model") or ""),
        updated_at=_float_value(value.get("updated_at"), time.time()),
        error=str(value.get("error") or ""),
    )


def format_token_k(tokens: int) -> str:
    value = _int_value(tokens)
    if value < 1000:
        return "小于1K"
    return f"{int(round(value / 1000.0))}K"


def format_context_usage_label(usage: ContextUsage | dict | None) -> str:
    if isinstance(usage, dict):
        usage = context_usage_from_dict(usage)
    if usage is None:
        return "上下文：刷新中"
    prefix = "约 " if not usage.exact else ""
    used = format_token_k(usage.used_tokens)
    if usage.context_window <= 0:
        return f"上下文：{prefix}{used}，窗口未知"
    window = format_token_k(usage.context_window)
    return f"上下文：{prefix}{used}/{window}，{usage.percent_used:.1f}%已用"


def context_window_for_model(model: str) -> int:
    return int(MODEL_CONTEXT_WINDOWS.get(str(model or "").strip(), DEFAULT_CONTEXT_WINDOW))


def estimate_text_tokens(text: str) -> int:
    content = str(text or "")
    if not content:
        return 0
    ascii_chars = sum(1 for ch in content if ord(ch) < 128)
    non_ascii_chars = len(content) - ascii_chars
    return max(1, int(round(ascii_chars / 4.0 + non_ascii_chars / 1.6)))


def estimate_turns_tokens(turns: list[dict], model: str = "") -> ContextUsage:
    total = estimate_text_tokens("请使用 Markdown 格式回答，尽量使用标题、段落、列表等结构化格式。不要使用任何表情符号（emoji）。")
    for turn in turns or []:
        total += estimate_text_tokens(str((turn or {}).get("question") or ""))
        answer = str((turn or {}).get("answer_md") or "")
        if answer and answer != "正在请求...":
            total += estimate_text_tokens(answer)
    return normalize_context_usage(
        used_tokens=total,
        context_window=context_window_for_model(model),
        source="estimated",
        exact=False,
        fresh=True,
        model=model,
    )
```

- [ ] **Step 4: Run the formatting test**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_context_usage_unit.py -v
```

Expected: all tests in `tests/test_context_usage_unit.py` pass.

- [ ] **Step 5: Commit Task 1**

Run:

```powershell
git add context_usage.py tests/test_context_usage_unit.py
git commit -m "feat: add context usage formatting helpers"
```

---

### Task 2: Answer List Top Row

**Files:**
- Modify: `main.py:2048-2109`, `main.py:5488-5494`
- Test: `tests/test_main_unit.py`

- [ ] **Step 1: Write failing UI row tests**

Append these tests near existing answer list rendering tests in `tests/test_main_unit.py`:

```python
def test_render_answer_list_inserts_context_usage_row_first(frame):
    frame._current_chat_state["context_usage"] = {
        "used_tokens": 113260,
        "context_window": 272000,
        "source": "openclaw",
        "exact": True,
        "fresh": True,
        "model": "gpt-5.4",
        "updated_at": 1.0,
    }
    frame.active_session_turns = [
        {"question": "你好", "answer_md": "你好，有什么可以帮你？", "model": "openai/gpt-5.2", "created_at": 1.0}
    ]

    frame._render_answer_list()

    assert frame.answer_list.GetString(0) == "上下文：113K/272K，41.6%已用"
    assert frame.answer_meta[0] == ("context_usage", -1, "上下文：113K/272K，41.6%已用", "")
    assert frame.answer_list.GetString(1) == "我"


def test_render_answer_list_keeps_empty_state_below_context_row(frame):
    frame._current_chat_state["context_usage"] = None
    frame.active_session_turns = []

    frame._render_answer_list()

    assert frame.answer_list.GetString(0) == "上下文：刷新中"
    assert frame.answer_meta[0] == ("context_usage", -1, "上下文：刷新中", "")
    assert frame.answer_list.GetString(1) == "暂无对话内容"


def test_focus_latest_answer_ignores_context_usage_row(frame, monkeypatch):
    frame.active_session_turns = [
        {"question": "q", "answer_md": "a", "model": "openai/gpt-5.2", "created_at": 1.0}
    ]
    frame._current_chat_state["context_usage"] = {
        "used_tokens": 12000,
        "context_window": 128000,
        "source": "estimated",
        "exact": False,
        "fresh": True,
        "model": "openai/gpt-5.2",
        "updated_at": 1.0,
    }
    monkeypatch.setattr(frame, "_can_focus_completion_result", lambda: True)

    frame._render_answer_list()
    frame._focus_latest_answer()

    selected = frame.answer_list.GetSelection()
    assert frame.answer_meta[selected][0] == "answer"
```

- [ ] **Step 2: Run the failing UI row tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_main_unit.py -k "context_usage_row or focus_latest_answer_ignores_context_usage_row" -v
```

Expected: fails because the context row is not rendered.

- [ ] **Step 3: Import formatter in `main.py`**

Add near the existing imports in `main.py`:

```python
from context_usage import (
    context_usage_from_dict,
    estimate_turns_tokens,
    format_context_usage_label,
)
```

- [ ] **Step 4: Add ChatFrame helper methods**

Add these methods before `_render_answer_list()` in `main.py`:

```python
    def _active_chat_context_usage(self):
        chat = self._current_chat_state if self.view_mode != "history" else self._find_archived_chat(self.view_history_id)
        usage = (chat or {}).get("context_usage") if isinstance(chat, dict) else None
        normalized = context_usage_from_dict(usage)
        if normalized is not None:
            return normalized
        turns = self._get_view_turns()
        model = self._resolve_current_model() if self.view_mode != "history" else str((chat or {}).get("model") or self.selected_model or DEFAULT_MODEL_ID)
        if turns:
            return estimate_turns_tokens(turns, model=model)
        return None

    def _append_context_usage_row(self) -> None:
        label = format_context_usage_label(self._active_chat_context_usage())
        self.answer_list.Append(label)
        self.answer_meta.append(("context_usage", -1, label, ""))
```

- [ ] **Step 5: Insert the row in `_render_answer_list()`**

Change the start of `_render_answer_list()` in `main.py`:

```python
        self.answer_list.Clear()
        self.answer_meta = []
        self._active_answer_row_index = -1
        self._append_context_usage_row()
        turns = self._get_view_turns()
```

Keep the existing empty state append, so empty lists become row 0 context usage and row 1 `暂无对话内容`.

- [ ] **Step 6: Run the UI row tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_main_unit.py -k "context_usage_row or focus_latest_answer_ignores_context_usage_row" -v
```

Expected: selected tests pass.

- [ ] **Step 7: Run nearby answer list tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_main_unit.py -k "render_answer_list or focus_latest_answer or startup_shows_last_active_turns_in_answer_list" -v
```

Expected: tests pass or only assertions that assumed row 0 was the first conversation row fail. Update those tests to search by row text or meta type instead of hard-coding row 0.

- [ ] **Step 8: Commit Task 2**

Run:

```powershell
git add main.py tests/test_main_unit.py
git commit -m "feat: show context usage row in answers"
```

---

### Task 3: Regular Model Usage and Estimation

**Files:**
- Modify: `chat_client.py:66-148`, `chat_client.py:151-192`
- Modify: `main.py:5329-5390`, `main.py:5415-5487`
- Test: `tests/test_chat_client_unit.py`, `tests/test_main_unit.py`

- [ ] **Step 1: Write failing ChatClient usage test**

Add to `tests/test_chat_client_unit.py`:

```python
import json

import chat_client
from chat_client import ChatClient


class _StreamingResponse:
    status_code = 200
    text = ""

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def iter_lines(self, decode_unicode=False):
        chunks = [
            {"choices": [{"delta": {"content": "你好"}}]},
            {"choices": [], "usage": {"prompt_tokens": 1200, "completion_tokens": 300, "total_tokens": 1500}},
        ]
        for chunk in chunks:
            yield ("data: " + json.dumps(chunk)).encode("utf-8")
        yield b"data: [DONE]"


def test_stream_chat_records_provider_usage(monkeypatch):
    monkeypatch.setattr(chat_client.requests, "post", lambda *args, **kwargs: _StreamingResponse())
    client = ChatClient(api_key="key", model="openai/gpt-5.2")

    result = client.stream_chat("你好", lambda _delta: None)

    assert result == "你好"
    assert client.last_context_usage["used_tokens"] == 1500
    assert client.last_context_usage["context_window"] == 128000
    assert client.last_context_usage["exact"] is True
    assert client.last_context_usage["source"] == "api"
```

- [ ] **Step 2: Run the failing ChatClient test**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_chat_client_unit.py::test_stream_chat_records_provider_usage -v
```

Expected: fails because `last_context_usage` is missing.

- [ ] **Step 3: Update `ChatClient` to capture usage**

In `chat_client.py`, import helper functions:

```python
from context_usage import context_window_for_model, normalize_context_usage
```

In `ChatClient.__init__`, add:

```python
        self.last_context_usage = None
```

In `_stream_request`, add `stream_options` and parse usage chunks:

```python
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
```

Inside the JSON chunk loop after `obj = json.loads(data)`:

```python
                    usage = obj.get("usage") if isinstance(obj.get("usage"), dict) else None
                    if usage:
                        self.last_context_usage = normalize_context_usage(
                            used_tokens=usage.get("total_tokens") or (
                                int(usage.get("prompt_tokens") or 0) + int(usage.get("completion_tokens") or 0)
                            ),
                            context_window=context_window_for_model(self.model),
                            source="api",
                            exact=True,
                            fresh=True,
                            model=self.model,
                        ).to_dict()
```

Make the same usage parsing addition in `_stream_doubao_request`.

- [ ] **Step 4: Run the ChatClient usage test**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_chat_client_unit.py::test_stream_chat_records_provider_usage -v
```

Expected: pass.

- [ ] **Step 5: Write failing main refresh tests for regular models**

Add to `tests/test_main_unit.py`:

```python
def test_on_done_uses_worker_context_usage_for_regular_model(frame):
    frame.active_session_turns = [{"question": "q", "answer_md": "", "model": "openai/gpt-5.2", "created_at": 1.0}]
    frame._current_chat_state["turns"] = frame.active_session_turns
    frame._pending_context_usage_by_turn = {
        0: {
            "used_tokens": 1500,
            "context_window": 128000,
            "source": "api",
            "exact": True,
            "fresh": True,
            "model": "openai/gpt-5.2",
            "updated_at": 1.0,
        }
    }

    frame._on_done(0, "answer", "", "openai/gpt-5.2", "", frame.active_chat_id)

    assert frame._current_chat_state["context_usage"]["used_tokens"] == 1500
    assert frame.answer_list.GetString(0) == "上下文：2K/128K，1.2%已用"


def test_on_done_estimates_regular_model_when_api_usage_missing(frame):
    frame.active_session_turns = [{"question": "你好", "answer_md": "", "model": "openai/gpt-5.2", "created_at": 1.0}]
    frame._current_chat_state["turns"] = frame.active_session_turns

    frame._on_done(0, "你好，有什么可以帮你？", "", "openai/gpt-5.2", "", frame.active_chat_id)

    usage = frame._current_chat_state["context_usage"]
    assert usage["source"] == "estimated"
    assert usage["exact"] is False
    assert frame.answer_list.GetString(0).startswith("上下文：约 ")
```

- [ ] **Step 6: Run failing main refresh tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_main_unit.py -k "worker_context_usage_for_regular_model or estimates_regular_model" -v
```

Expected: fails because `_pending_context_usage_by_turn` and refresh logic are missing.

- [ ] **Step 7: Store regular model usage from worker**

In `ChatFrame.__init__` in `main.py`, add:

```python
        self._pending_context_usage_by_turn = {}
```

In `_worker`, after regular model `full = c.stream_chat(...)`, add:

```python
                if getattr(c, "last_context_usage", None):
                    self._pending_context_usage_by_turn[turn_idx] = c.last_context_usage
```

In the fallback model success branch, after `full = c.stream_chat(...)`, add the same assignment.

- [ ] **Step 8: Add main refresh helper**

Add this helper near other chat state helpers in `main.py`:

```python
    def _set_chat_context_usage(self, chat: dict, usage: dict | None) -> None:
        if not isinstance(chat, dict) or not isinstance(usage, dict):
            return
        chat["context_usage"] = usage

    def _refresh_context_usage_after_done(self, target_chat: dict, target_turns: list, turn_idx: int, model: str) -> None:
        usage = self._pending_context_usage_by_turn.pop(turn_idx, None)
        if not usage and not (is_codex_model(model) or is_claudecode_model(model) or is_openclaw_model(model)):
            usage = estimate_turns_tokens(target_turns, model=model).to_dict()
        self._set_chat_context_usage(target_chat, usage)
```

Call it in `_on_done()` after answer text and attachments are finalized:

```python
            self._refresh_context_usage_after_done(target_chat, target_turns, turn_idx, used_model)
```

- [ ] **Step 9: Run regular model tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_chat_client_unit.py tests/test_main_unit.py -k "context_usage or worker_context_usage_for_regular_model or estimates_regular_model" -v
```

Expected: selected tests pass.

- [ ] **Step 10: Commit Task 3**

Run:

```powershell
git add chat_client.py main.py tests/test_chat_client_unit.py tests/test_main_unit.py
git commit -m "feat: capture regular model context usage"
```

---

### Task 4: ClaudeCode Exact Usage

**Files:**
- Modify: `claudecode_client.py:45-180`
- Modify: `main.py:5329-5390`
- Test: `tests/test_claudecode_client_unit.py`, `tests/test_main_unit.py`

- [ ] **Step 1: Write failing ClaudeCode parser test**

Add to `tests/test_claudecode_client_unit.py`:

```python
import json

from claudecode_client import ClaudeCodeClient


class _Result:
    returncode = 0
    stderr = ""


class _Manager:
    def run(self, request, on_output=None):
        payloads = [
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "text", "text": "完成"}],
                    "usage": {"input_tokens": 31747, "output_tokens": 1},
                },
                "session_id": "sid-1",
            },
            {
                "type": "result",
                "session_id": "sid-1",
                "modelUsage": {
                    "claude-haiku-4-5-20251001": {
                        "inputTokens": 795,
                        "outputTokens": 99,
                        "cacheReadInputTokens": 0,
                        "cacheCreationInputTokens": 29798,
                        "contextWindow": 200000,
                    }
                },
            },
        ]
        for payload in payloads:
            on_output(json.dumps(payload, ensure_ascii=False) + "\n")
        return _Result()


def test_claudecode_stream_chat_records_model_usage():
    client = ClaudeCodeClient(cli_manager=_Manager())

    full, session_id = client.stream_chat("修复问题")

    assert full == "完成"
    assert session_id == "sid-1"
    assert client.last_context_usage["used_tokens"] == 30692
    assert client.last_context_usage["context_window"] == 200000
    assert client.last_context_usage["source"] == "claudecode"
    assert client.last_context_usage["model"] == "claude-haiku-4-5-20251001"
```

- [ ] **Step 2: Run the failing ClaudeCode parser test**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_claudecode_client_unit.py::test_claudecode_stream_chat_records_model_usage -v
```

Expected: fails because `last_context_usage` is missing.

- [ ] **Step 3: Implement ClaudeCode usage capture**

In `claudecode_client.py`, import:

```python
from context_usage import normalize_context_usage
```

In `ClaudeCodeClient.__init__`, add:

```python
        self.last_context_usage = None
```

Inside `_process_line`, in the `msg_type == "result"` block after session id handling, add:

```python
                model_usage = obj.get("modelUsage") if isinstance(obj.get("modelUsage"), dict) else {}
                if model_usage:
                    model_name, stats = next(iter(model_usage.items()))
                    if isinstance(stats, dict):
                        used_tokens = (
                            int(stats.get("inputTokens") or 0)
                            + int(stats.get("outputTokens") or 0)
                            + int(stats.get("cacheReadInputTokens") or 0)
                            + int(stats.get("cacheCreationInputTokens") or 0)
                        )
                        self.last_context_usage = normalize_context_usage(
                            used_tokens=used_tokens,
                            context_window=stats.get("contextWindow") or 0,
                            source="claudecode",
                            exact=True,
                            fresh=True,
                            model=str(model_name or ""),
                        ).to_dict()
```

- [ ] **Step 4: Run the ClaudeCode parser test**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_claudecode_client_unit.py::test_claudecode_stream_chat_records_model_usage -v
```

Expected: pass.

- [ ] **Step 5: Store ClaudeCode usage in `_worker`**

In `main.py`, after `full, new_session_id = client.stream_chat(...)`, add:

```python
                if getattr(client, "last_context_usage", None):
                    self._pending_context_usage_by_turn[turn_idx] = client.last_context_usage
```

- [ ] **Step 6: Write and run main ClaudeCode usage test**

Add to `tests/test_main_unit.py`:

```python
def test_on_done_uses_pending_claudecode_context_usage(frame):
    frame.active_session_turns = [{"question": "q", "answer_md": "", "model": "claudecode/default", "created_at": 1.0}]
    frame._current_chat_state["turns"] = frame.active_session_turns
    frame._pending_context_usage_by_turn = {
        0: {
            "used_tokens": 30692,
            "context_window": 200000,
            "source": "claudecode",
            "exact": True,
            "fresh": True,
            "model": "claude-haiku-4-5-20251001",
            "updated_at": 1.0,
        }
    }

    frame._on_done(0, "完成", "", "claudecode/default", "", frame.active_chat_id)

    assert frame._current_chat_state["context_usage"]["source"] == "claudecode"
    assert frame.answer_list.GetString(0) == "上下文：31K/200K，15.3%已用"
```

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_main_unit.py::test_on_done_uses_pending_claudecode_context_usage -v
```

Expected: pass after Step 5.

- [ ] **Step 7: Commit Task 4**

Run:

```powershell
git add claudecode_client.py main.py tests/test_claudecode_client_unit.py tests/test_main_unit.py
git commit -m "feat: capture claudecode context usage"
```

---

### Task 5: OpenClaw Exact Usage

**Files:**
- Modify: `openclaw_client.py`
- Modify: `main.py:2646-2677`
- Test: `tests/test_openclaw_client_unit.py`, `tests/test_main_unit.py`

- [ ] **Step 1: Write failing OpenClaw usage parser test**

Add to `tests/test_openclaw_client_unit.py`:

```python
from openclaw_client import find_openclaw_session_usage


def test_find_openclaw_session_usage_matches_session_id():
    payload = {
        "sessions": [
            {
                "sessionId": "zgwd-1",
                "inputTokens": 620,
                "outputTokens": 40,
                "totalTokens": 113260,
                "totalTokensFresh": True,
                "contextTokens": 272000,
                "model": "gpt-5.4",
                "modelProvider": "openai-codex",
            }
        ]
    }

    usage = find_openclaw_session_usage(payload, "zgwd-1")

    assert usage["used_tokens"] == 113260
    assert usage["context_window"] == 272000
    assert usage["source"] == "openclaw"
    assert usage["exact"] is True
    assert usage["fresh"] is True
    assert usage["model"] == "gpt-5.4"
```

- [ ] **Step 2: Run the failing OpenClaw parser test**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_openclaw_client_unit.py::test_find_openclaw_session_usage_matches_session_id -v
```

Expected: fails because `find_openclaw_session_usage` does not exist.

- [ ] **Step 3: Add OpenClaw usage helpers**

In `openclaw_client.py`, import:

```python
from context_usage import normalize_context_usage
```

Add:

```python
def find_openclaw_session_usage(payload: dict, session_id: str) -> dict | None:
    target = str(session_id or "").strip()
    if not target or not isinstance(payload, dict):
        return None
    sessions = payload.get("sessions") if isinstance(payload.get("sessions"), list) else []
    for item in sessions:
        if not isinstance(item, dict):
            continue
        if str(item.get("sessionId") or "").strip() != target:
            continue
        return normalize_context_usage(
            used_tokens=item.get("totalTokens") or (
                int(item.get("inputTokens") or 0) + int(item.get("outputTokens") or 0)
            ),
            context_window=item.get("contextTokens") or 0,
            source="openclaw",
            exact=True,
            fresh=bool(item.get("totalTokensFresh", True)),
            model=str(item.get("model") or ""),
        ).to_dict()
    return None
```

- [ ] **Step 4: Run the OpenClaw parser test**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_openclaw_client_unit.py::test_find_openclaw_session_usage_matches_session_id -v
```

Expected: pass.

- [ ] **Step 5: Add main OpenClaw refresh hook**

In `main.py`, import:

```python
    find_openclaw_session_usage,
```

from `openclaw_client`.

Add a method:

```python
    def _refresh_openclaw_context_usage_from_sessions(self) -> None:
        session_id = str(self.active_openclaw_session_id or "").strip()
        if not session_id:
            return

        def _worker():
            try:
                import json
                import subprocess

                completed = subprocess.run(
                    ["openclaw", "sessions", "--json"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=30,
                    check=False,
                )
                if completed.returncode != 0:
                    return
                payload = json.loads(completed.stdout)
                usage = find_openclaw_session_usage(payload, session_id)
                if usage:
                    wx_call_after_if_alive(self._apply_current_chat_context_usage, usage)
            except Exception:
                return

        threading.Thread(target=_worker, daemon=True).start()
```

Add:

```python
    def _apply_current_chat_context_usage(self, usage: dict) -> None:
        self._set_chat_context_usage(self._current_chat_state, usage)
        self._save_state()
        if self.view_mode == "active":
            self._render_answer_list()
```

In `_apply_openclaw_sync_batch`, after `assistant_changed` is known and before `_save_state()`, call:

```python
        if assistant_changed:
            self._refresh_openclaw_context_usage_from_sessions()
```

- [ ] **Step 6: Write main OpenClaw refresh test with direct apply**

Add to `tests/test_main_unit.py`:

```python
def test_apply_current_chat_context_usage_updates_answer_row(frame):
    usage = {
        "used_tokens": 113260,
        "context_window": 272000,
        "source": "openclaw",
        "exact": True,
        "fresh": True,
        "model": "gpt-5.4",
        "updated_at": 1.0,
    }
    frame.active_session_turns = [{"question": "q", "answer_md": "a", "model": "openclaw/main", "created_at": 1.0}]
    frame._current_chat_state["turns"] = frame.active_session_turns

    frame._apply_current_chat_context_usage(usage)

    assert frame._current_chat_state["context_usage"]["source"] == "openclaw"
    assert frame.answer_list.GetString(0) == "上下文：113K/272K，41.6%已用"
```

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_main_unit.py::test_apply_current_chat_context_usage_updates_answer_row -v
```

Expected: pass.

- [ ] **Step 7: Commit Task 5**

Run:

```powershell
git add openclaw_client.py main.py tests/test_openclaw_client_unit.py tests/test_main_unit.py
git commit -m "feat: capture openclaw context usage"
```

---

### Task 6: Codex Exact Usage

**Files:**
- Modify: `codex_client.py:151-168`, `codex_client.py:490-700`
- Modify: `main.py:3875-4005`
- Test: `tests/test_codex_client_unit.py`, `tests/test_main_unit.py`

- [ ] **Step 1: Write failing Codex token event test**

Add to `tests/test_codex_client_unit.py`:

```python
from codex_client import CodexAppServerClient


def test_codex_protocol_token_count_event_normalizes_usage():
    events = []
    client = CodexAppServerClient(on_event=events.append)
    client._emit_protocol_event(
        "token_count",
        {
            "info": {
                "total_token_usage": {"total_tokens": 44176},
                "last_token_usage": {"total_tokens": 11891},
                "model_context_window": 258400,
            }
        },
        {"method": "token_count"},
    )

    assert events[-1].type == "token_count"
    assert events[-1].data["context_usage"]["used_tokens"] == 44176
    assert events[-1].data["context_usage"]["context_window"] == 258400
    assert events[-1].data["context_usage"]["source"] == "codex"
```

- [ ] **Step 2: Run the failing Codex event test**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_codex_client_unit.py::test_codex_protocol_token_count_event_normalizes_usage -v
```

Expected: fails because `token_count` handling is missing.

- [ ] **Step 3: Extend `CodexEvent` and event parsing**

In `codex_client.py`, import:

```python
from context_usage import normalize_context_usage
```

Add a `usage` field to `CodexEvent`:

```python
    usage: dict = field(default_factory=dict)
```

At the top of `_emit_protocol_event`, before other method checks, add:

```python
        payload_type = str(params.get("type") or method or "").strip()
        info = params.get("info") if isinstance(params.get("info"), dict) else params
        if payload_type == "token_count" or method == "token_count":
            total = info.get("total_token_usage") if isinstance(info.get("total_token_usage"), dict) else {}
            usage = normalize_context_usage(
                used_tokens=total.get("total_tokens") or 0,
                context_window=info.get("model_context_window") or 0,
                source="codex",
                exact=True,
                fresh=True,
                model="codex/main",
            ).to_dict()
            self._emit_event(CodexEvent(type="token_count", usage=usage, data={"context_usage": usage, **params}))
            return
```

- [ ] **Step 4: Run the Codex event test**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_codex_client_unit.py::test_codex_protocol_token_count_event_normalizes_usage -v
```

Expected: pass.

- [ ] **Step 5: Apply Codex usage events in main**

In `_on_codex_event_for_chat` in `main.py`, add a branch before item handling:

```python
        if event.type == "token_count" and event.usage:
            target_chat = self._current_chat_state
            if chat_id and chat_id not in {self.active_chat_id, self.current_chat_id, ""}:
                archived = self._find_archived_chat(chat_id)
                if isinstance(archived, dict):
                    target_chat = archived
            self._set_chat_context_usage(target_chat, event.usage)
            self._save_state()
            if chat_id in {self.active_chat_id, self.current_chat_id, ""} and self.view_mode == "active":
                self._render_answer_list()
            return
```

- [ ] **Step 6: Write and run main Codex event test**

Add to `tests/test_main_unit.py`:

```python
def test_codex_token_count_event_updates_context_usage(frame):
    event = main.CodexEvent(
        type="token_count",
        usage={
            "used_tokens": 44176,
            "context_window": 258400,
            "source": "codex",
            "exact": True,
            "fresh": True,
            "model": "codex/main",
            "updated_at": 1.0,
        },
    )

    frame._on_codex_event_for_chat(frame.active_chat_id or frame.current_chat_id or "", event)

    assert frame._current_chat_state["context_usage"]["source"] == "codex"
    assert frame.answer_list.GetString(0) == "上下文：44K/258K，17.1%已用"
```

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_main_unit.py::test_codex_token_count_event_updates_context_usage -v
```

Expected: pass.

- [ ] **Step 7: Commit Task 6**

Run:

```powershell
git add codex_client.py main.py tests/test_codex_client_unit.py tests/test_main_unit.py
git commit -m "feat: capture codex context usage"
```

---

### Task 7: State Persistence and Refresh Triggers

**Files:**
- Modify: `main.py:1280-1420`, `main.py:2153-2160`, `main.py:4030-4040`, `main.py:5600-5665`
- Test: `tests/test_main_unit.py`

- [ ] **Step 1: Write failing persistence and model-change tests**

Add to `tests/test_main_unit.py`:

```python
def test_context_usage_persists_in_saved_state(frame):
    frame._current_chat_state["context_usage"] = {
        "used_tokens": 12000,
        "context_window": 128000,
        "source": "estimated",
        "exact": False,
        "fresh": True,
        "model": "openai/gpt-5.2",
        "updated_at": 1.0,
    }

    frame._save_state()
    data = json.loads(frame.state_path.read_text(encoding="utf-8"))

    assert data["active_chat"]["context_usage"]["used_tokens"] == 12000


def test_model_change_rerenders_context_usage_with_new_window(frame):
    frame.active_session_turns = [{"question": "你好", "answer_md": "你好", "model": "openai/gpt-5.2", "created_at": 1.0}]
    frame._current_chat_state["turns"] = frame.active_session_turns
    frame._current_chat_state["context_usage"] = None
    frame.model_combo.SetValue("anthropic/claude-sonnet-4.6")

    frame._on_model_changed(None)

    assert frame.answer_list.GetString(0).startswith("上下文：约 ")
    assert "/200K" in frame.answer_list.GetString(0)
```

- [ ] **Step 2: Run failing persistence tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_main_unit.py -k "context_usage_persists or model_change_rerenders_context_usage" -v
```

Expected: at least the model-change test fails until refresh and render are connected.

- [ ] **Step 3: Ensure state payload preserves context usage**

`_save_state()` already deep-copies `_current_chat_state` into `active_chat`, so active-chat persistence should not require new serialization code. If implementation changes reveal a manual active chat payload branch without the key, include:

```python
"context_usage": copy.deepcopy(self._current_chat_state.get("context_usage")),
```

When switching chats, keep the saved `context_usage` on the loaded chat and call `_render_answer_list()`.

- [ ] **Step 4: Refresh on model change**

At the end of `_on_model_changed`, after `_save_state()`, add:

```python
            if self.view_mode == "active":
                self._current_chat_state["context_usage"] = estimate_turns_tokens(
                    self.active_session_turns,
                    model=self.selected_model,
                ).to_dict() if self.active_session_turns else None
                self._render_answer_list()
```

- [ ] **Step 5: Clear context usage on new chat**

Where new chat state is created in `_start_remote_new_chat()` and `_on_new_chat_clicked()`, ensure the new `_current_chat_state` contains:

```python
"context_usage": None,
```

- [ ] **Step 6: Run persistence tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_main_unit.py -k "context_usage_persists or model_change_rerenders_context_usage or new_chat" -v
```

Expected: selected tests pass.

- [ ] **Step 7: Commit Task 7**

Run:

```powershell
git add main.py tests/test_main_unit.py
git commit -m "feat: persist context usage state"
```

---

### Task 8: Final Regression Pass

**Files:**
- Modify only files needed to fix failures found by this task.

- [ ] **Step 1: Run focused context usage tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_context_usage_unit.py tests/test_chat_client_unit.py tests/test_claudecode_client_unit.py tests/test_codex_client_unit.py tests/test_openclaw_client_unit.py tests/test_main_unit.py -k "context_usage or token_count or model_usage or openclaw_session_usage" -v
```

Expected: all selected tests pass.

- [ ] **Step 2: Run answer list and CLI-adjacent tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_main_unit.py tests/test_openclaw_integration.py tests/test_openclaw_e2e.py tests/test_codex_client_unit.py tests/test_claudecode_client_unit.py -k "answer_list or openclaw or codex or claudecode" -v
```

Expected: all selected tests pass. If unrelated pre-existing failures appear, document them with exact test names and do not change unrelated behavior.

- [ ] **Step 3: Run diff check**

Run:

```powershell
git diff --check
```

Expected: no whitespace errors.

- [ ] **Step 4: Commit final fixes if any**

If Step 1 or Step 2 required fixes, run:

```powershell
git add context_usage.py main.py chat_client.py claudecode_client.py codex_client.py openclaw_client.py tests
git commit -m "test: cover context usage indicator"
```

If no files changed after Task 7, skip this commit.
