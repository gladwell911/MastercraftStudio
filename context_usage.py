from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


DEFAULT_CONTEXT_WINDOW = 128000

MODEL_CONTEXT_WINDOWS = {
    "codex/main": 258400,
    "gpt-5-codex": 258400,
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

    def __post_init__(self) -> None:
        self.percent_used = (
            round((self.used_tokens / self.context_window * 100.0), 1)
            if self.context_window > 0
            else 0.0
        )

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
        return "\u5c0f\u4e8e1K"
    return f"{(value + 500) // 1000}K"


def format_context_usage_label(usage: ContextUsage | dict | None) -> str:
    if isinstance(usage, dict):
        usage = context_usage_from_dict(usage)
    if usage is None:
        return "\u6682\u65e0"
    if usage.used_tokens <= 0 or usage.context_window <= 0:
        return "\u6682\u65e0"
    used = format_token_k(usage.used_tokens).lower()
    window = format_token_k(usage.context_window).lower()
    return f"{used} / {window}"


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
    total = estimate_text_tokens(
        "\u8bf7\u4f7f\u7528 Markdown \u683c\u5f0f\u56de\u7b54\uff0c\u5c3d\u91cf\u4f7f\u7528\u6807\u9898\u3001\u6bb5\u843d\u3001\u5217\u8868\u7b49\u7ed3\u6784\u5316\u683c\u5f0f\u3002\u4e0d\u8981\u4f7f\u7528\u4efb\u4f55\u8868\u60c5\u7b26\u53f7\uff08emoji\uff09\u3002"
    )
    for turn in turns or []:
        total += estimate_text_tokens(str((turn or {}).get("question") or ""))
        answer = str((turn or {}).get("answer_md") or "")
        if answer and answer != "\u6b63\u5728\u8bf7\u6c42...":
            total += estimate_text_tokens(answer)
    return normalize_context_usage(
        used_tokens=total,
        context_window=context_window_for_model(model),
        source="estimated",
        exact=False,
        fresh=True,
        model=model,
    )
