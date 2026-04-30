from context_usage import (
    ContextUsage,
    context_usage_from_dict,
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
    assert format_token_k(12500) == "13K"
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


def test_context_usage_to_dict_computes_percent_for_direct_construction():
    usage = ContextUsage(
        used_tokens=113260,
        context_window=272000,
        source="openclaw",
        exact=True,
        fresh=True,
        model="gpt-5.4",
        updated_at=1.0,
    )

    assert usage.to_dict()["percent_used"] == 41.6


def test_context_usage_dict_round_trip_preserves_values():
    usage = ContextUsage(
        used_tokens=113260,
        context_window=272000,
        source="openclaw",
        exact=True,
        fresh=True,
        model="gpt-5.4",
        updated_at=1.0,
    )

    restored = context_usage_from_dict(usage.to_dict())

    assert restored == ContextUsage(
        used_tokens=113260,
        context_window=272000,
        percent_used=41.6,
        source="openclaw",
        exact=True,
        fresh=True,
        model="gpt-5.4",
        updated_at=1.0,
        error="",
    )
