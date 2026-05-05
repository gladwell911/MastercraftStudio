from context_usage import (
    ContextUsage,
    context_usage_from_dict,
    format_context_usage_label,
    format_token_k,
    normalize_context_usage,
)
from codex_client import codex_context_usage_from_payload


def test_format_token_k_uses_less_than_one_k_for_small_values():
    assert format_token_k(0) == "\u5c0f\u4e8e1K"
    assert format_token_k(999) == "\u5c0f\u4e8e1K"


def test_format_token_k_rounds_to_integer_k():
    assert format_token_k(1000) == "1K"
    assert format_token_k(12400) == "12K"
    assert format_token_k(12500) == "13K"
    assert format_token_k(12600) == "13K"


def test_exact_context_label_shows_used_and_total_only():
    usage = ContextUsage(
        used_tokens=113260,
        context_window=272000,
        source="openclaw",
        exact=True,
        fresh=True,
        model="gpt-5.4",
        updated_at=1.0,
    )

    assert format_context_usage_label(usage) == "113k / 272k"


def test_estimated_context_label_still_shows_used_and_total_only():
    usage = ContextUsage(
        used_tokens=12400,
        context_window=128000,
        source="estimated",
        exact=False,
        fresh=True,
        model="openai/gpt-5.2",
        updated_at=1.0,
    )

    assert format_context_usage_label(usage) == "12k / 128k"


def test_unknown_window_label_is_missing():
    usage = ContextUsage(
        used_tokens=12400,
        context_window=0,
        source="codex",
        exact=True,
        fresh=True,
        model="gpt-5.4",
        updated_at=1.0,
    )

    assert format_context_usage_label(usage) == "\u6682\u65e0"


def test_estimated_unknown_window_label_is_missing():
    usage = ContextUsage(
        used_tokens=44176,
        context_window=0,
        source="codex",
        exact=False,
        fresh=True,
        model="gpt-5-codex",
        updated_at=1.0,
    )

    assert format_context_usage_label(usage) == "\u6682\u65e0"


def test_missing_usage_label_is_missing():
    assert format_context_usage_label(None) == "\u6682\u65e0"


def test_codex_usage_payload_falls_back_to_model_context_window():
    usage = codex_context_usage_from_payload(
        {
            "usage": {
                "inputTokens": 40000,
                "outputTokens": 4176,
                "cacheReadInputTokens": 0,
                "cacheCreationInputTokens": 0,
                "model": "codex/main",
            }
        }
    )

    assert usage["used_tokens"] == 44176
    assert usage["context_window"] == 258400
    assert format_context_usage_label(usage) == "44k / 258k"


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
