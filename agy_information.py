"""Measurements from AGY's ACP prompt and native /usage command."""

from datetime import datetime, timezone
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory
import time

from agy_acp import AGY_MODEL_ID, find_agy_executable
from chat_information import format_quota_reset_time


AGY_QUOTA_WINDOWS = {
    ("Gemini Models", "Weekly Limit Remaining"): "周限额",
    ("Gemini Models", "Five Hour Limit Remaining"): "5 小时限额",
    ("Claude and GPT models", "Weekly Limit Remaining"): "Claude 和 GPT 共用周限额",
}
AGY_QUOTA_MAX_AGE_SECONDS = 60


def agy_usage_owner(chat: dict) -> list:
    return [chat.get(key) for key in ("agy_session_id", "agy_native_owner", "native_account", "native_identity")]


def agy_context_tokens(update: dict) -> list[int] | None:
    used, size = update.get("used"), update.get("size")
    if type(used) is int and type(size) is int and used >= 0 and size > 0:
        return [used, size]
    return None


def agy_session_rows(chat: dict, turns: list[dict]) -> list[str]:
    owner = agy_usage_owner(chat)
    context = None
    settled = {}
    for index, turn in enumerate(turns if owner[0] and owner[1] else ()):
        if (turn.get("model") != AGY_MODEL_ID or turn.get("local_command")
                or turn.get("session_id") not in (None, "", owner[0])
                or turn.get("agy_session_id") not in (None, "", owner[0])):
            continue
        owned = turn.get("agy_usage_owner") == owner
        if owned and turn.get("agy_context_tokens") is not None:
            context = turn["agy_context_tokens"]
        if turn.get("request_status") in {"pending", "running", "queued"} or turn.get("assistant_only"):
            continue
        key = turn.get("agy_native_turn_id") or turn.get("_provider_request_id") or index
        usage = turn.get("agy_prompt_usage") if owned else None
        if key not in settled or usage:
            settled[key] = (usage, bool(turn.get("agy_usage_partial")))

    current = next(reversed(settled.values()))[0] if settled else None

    def count(usage, field):
        value = usage.get(field) if isinstance(usage, dict) else None
        return value if type(value) is int and value >= 0 else None

    def total(field, unit=""):
        counts = [value for usage, _ in settled.values() if (value := count(usage, field)) is not None]
        if not counts:
            return "暂不可用"
        value = f"{sum(counts):,}{unit}"
        if len(counts) < len(settled) or any(partial for _, partial in settled.values()):
            return f"完整累计暂不可用（已记录 {value}）"
        return value

    context_text = f"{context[0]:,} / {context[1]:,} token" if context is not None else "暂不可用"
    last_hit = count(current, "cachedReadTokens")
    last_text = f"{last_hit:,} token" if last_hit is not None else "暂不可用"
    return [
        f"当前上下文：{context_text}",
        f"会话累计 token：{total('totalTokens')}",
        f"当前缓存命中：{last_text}",
        f"累计缓存命中：{total('cachedReadTokens', ' token')}",
    ]


def read_agy_quota() -> tuple[str, dict, float]:
    executable = find_agy_executable()
    if not executable:
        return "未安装 AGY", {}, time.monotonic()
    try:
        with TemporaryDirectory(prefix="mc-agy-usage-") as cwd:
            result = subprocess.run(
                [executable, "--print", "/usage", "--print-timeout", "10s",
                 "--log-file", str(Path(cwd) / "agy.log")],
                cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
    except (OSError, subprocess.TimeoutExpired):
        return "查询失败", {}, time.monotonic()
    observed_at = time.monotonic()
    rows = {}
    for line in result.stdout.splitlines():
        columns = [column.strip() for column in line.split("\t")]
        if len(columns) != 4:
            continue
        group, window, percent, reset = columns
        label = AGY_QUOTA_WINDOWS.get((group, window))
        if label:
            match = re.fullmatch(r"(\d{1,3})(?:\.\d+)?%", percent)
            remaining = percent if match and 0 <= float(percent[:-1]) <= 100 else None
            rows[label] = (remaining, reset)
    if result.returncode == 0 and rows:
        return "", rows, observed_at
    message = (result.stderr + "\n" + result.stdout).casefold()
    if any(marker in message for marker in (
        "not logged in", "not authenticated", "login required", "authentication required",
        "please log in", "please sign in",
    )):
        return "未登录", {}, observed_at
    if "quota" in message and ("not applicable" in message or "not supported for" in message):
        return "当前账号不适用", {}, observed_at
    return ("查询失败" if result.returncode else "暂不可用"), {}, observed_at


def agy_quota_rows(result: tuple[str, dict, float] | None) -> list[str]:
    rows = ["AGY 额度来源：查询时的本机 AGY 账号"]
    error, quota, observed_at = result if result is not None else ("查询中", {}, time.monotonic())
    if time.monotonic() - observed_at >= AGY_QUOTA_MAX_AGE_SECONDS:
        error = "已过期"
    for label in AGY_QUOTA_WINDOWS.values():
        if error:
            rows.append(f"{label}：{error}")
            continue
        remaining, raw_reset = quota.get(label, (None, None))
        reset = None
        try:
            value = datetime.fromisoformat(raw_reset.replace("Z", "+00:00")) if raw_reset else None
            if value is not None and value.tzinfo is not None and value > datetime.fromtimestamp(0, timezone.utc):
                if value < datetime.now(timezone.utc):
                    rows.append(f"{label}：已过期")
                    continue
                reset = format_quota_reset_time(raw_reset)
        except (ValueError, OverflowError, OSError):
            pass
        remaining = f"剩余 {remaining}" if remaining is not None else "剩余量暂不可用"
        rows.append(f"{label}：{remaining}，{'重置 ' + reset if reset else '重置时间暂不可用'}")
    return rows
