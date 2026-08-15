import time
from datetime import datetime

import main


def _ts(y, m, d, hh=0, mm=0):
    return datetime(y, m, d, hh, mm).timestamp()


def _make_turn(question, answer, created_at, model="openai/gpt-5.2"):
    return {
        "question": question,
        "answer_md": answer,
        "model": model,
        "created_at": created_at,
    }


def test_wechat_time_label_same_day():
    now = _ts(2026, 8, 12, 15, 0)

    assert main.wechat_time_label(_ts(2026, 8, 12, 9, 5), now) == "09:05"
    assert main.wechat_time_label(_ts(2026, 8, 12, 0, 0), now) == "00:00"


def test_wechat_time_label_yesterday():
    now = _ts(2026, 8, 12, 15, 0)

    assert main.wechat_time_label(_ts(2026, 8, 11, 23, 40), now) == "昨天 23:40"


def test_wechat_time_label_within_week_shows_weekday():
    now = _ts(2026, 8, 12, 15, 0)  # Wednesday

    assert main.wechat_time_label(_ts(2026, 8, 10, 8, 0), now) == "星期一 08:00"  # 2 天前
    assert main.wechat_time_label(_ts(2026, 8, 5, 12, 30), now) == "星期三 12:30"  # 7 天前


def test_wechat_time_label_older_shows_full_date():
    now = _ts(2026, 8, 12, 15, 0)

    assert main.wechat_time_label(_ts(2026, 8, 4, 8, 0), now) == "2026年8月4日 08:00"  # 8 天前
    assert main.wechat_time_label(_ts(2025, 12, 31, 18, 30), now) == "2025年12月31日 18:30"  # 跨年


def test_should_show_time_first_message_always_shows():
    assert main.should_show_time(None, 1000.0) is True


def test_should_show_time_gap_boundary():
    assert main.should_show_time(1000.0, 1300.0) is False  # 恰好 300 秒不显示
    assert main.should_show_time(1000.0, 1300.1) is True  # 超过 300 秒才显示
    assert main.should_show_time(1000.0, 1299.9) is False


def test_should_show_time_invalid_prev_falls_back_to_show():
    assert main.should_show_time("not-a-number", 1000.0) is True


def test_answer_list_inserts_time_rows_by_wechat_rule(frame):
    now = time.time()
    frame.active_session_turns = [
        _make_turn("q0", "a0", now - 1000),
        _make_turn("q1", "a1", now - 800),  # 距上一条 200 秒，不显示时间
        _make_turn("q2", "a2", now - 100),  # 距上一条 700 秒，显示时间
    ]
    frame.view_mode = "active"

    frame._render_answer_list()

    kinds = [meta[0] for meta in frame.answer_meta]
    assert kinds == [
        "time", "user", "question", "ai", "answer",
        "user", "question", "ai", "answer",
        "time", "user", "question", "ai", "answer",
    ]
    assert frame.answer_meta[0][1] == 0
    assert frame.answer_meta[9][1] == 2
    assert frame.answer_meta[0][2] == main.wechat_time_label(now - 1000, time.time())
    assert frame.answer_meta[9][2] == main.wechat_time_label(now - 100, time.time())
    for row, meta in enumerate(frame.answer_meta):
        assert frame.answer_list.GetString(row) == meta[2]


def test_answer_list_time_rows_do_not_break_turn_lookup(frame, monkeypatch):
    copied = []
    monkeypatch.setattr(frame, "_set_clipboard_text", lambda text: copied.append(text) or True)
    now = time.time()
    frame.active_session_turns = [
        _make_turn("q0", "a0", now - 1000),
        _make_turn("q1", "a1", now - 100),
    ]
    frame.view_mode = "active"
    frame._render_answer_list()

    answer_row = next(i for i, meta in enumerate(frame.answer_meta) if meta[0] == "answer" and meta[1] == 1)
    frame.answer_list.SetSelection(answer_row)
    assert frame._copy_selected_answer_to_clipboard() is True
    assert copied == ["a1"]
    assert frame._selected_answer_source_text() == "a1"

    time_row = next(i for i, meta in enumerate(frame.answer_meta) if meta[0] == "time")
    frame.answer_list.SetSelection(time_row)
    assert frame._copy_selected_answer_to_clipboard() is False
    assert frame._selected_answer_text_viewer_content() is None
    assert frame._try_open_selected_answer_detail() is False


def test_answer_list_time_row_id_is_stable_and_unique(frame):
    assert frame._answer_row_id(("time", 2, "14:32", "")) == "time:2"
    now = time.time()
    frame.active_session_turns = [
        _make_turn("q0", "a0", now - 1000),
        _make_turn("q1", "a1", now - 100),
    ]
    frame.view_mode = "active"
    frame._render_answer_list()

    ids = list(frame.answer_list_model.visible_ids)
    assert "time:0" in ids
    assert "time:1" in ids
    assert len(ids) == len(set(ids))

    strings_before = [frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())]
    frame._render_answer_list()
    strings_after = [frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())]
    assert strings_after == strings_before


def test_answer_list_time_row_selection_survives_refresh(frame):
    now = time.time()
    frame.active_session_turns = [
        _make_turn("q0", "a0", now - 1000),
        _make_turn("q1", "a1", now - 100),
    ]
    frame.view_mode = "active"
    frame._render_answer_list()
    time_row = next(i for i, meta in enumerate(frame.answer_meta) if meta[0] == "time" and meta[1] == 1)
    frame.answer_list.SetSelection(time_row)

    frame._refresh_answer_list_preserving_selection()

    assert frame.answer_list.GetSelection() == next(
        i for i, meta in enumerate(frame.answer_meta) if meta[0] == "time" and meta[1] == 1
    )


def test_answer_list_time_rows_keep_tail_notice(frame):
    now = time.time()
    frame.active_session_turns = [_make_turn("q0", "a0", now - 100)]
    frame.view_mode = "active"
    frame._answer_list_tail_notice = "以开启新会话"
    frame._answer_list_tail_notice_chat_id = ""

    frame._render_answer_list()

    assert frame.answer_meta[0][0] == "time"
    assert frame.answer_meta[-1][0] == "notice"
    assert frame.answer_list.GetString(frame.answer_list.GetCount() - 1) == "以开启新会话"


def test_answer_list_turn_without_created_at_gets_no_time_row(frame):
    now = time.time()
    frame.active_session_turns = [
        {"question": "q0", "answer_md": "a0", "model": "openai/gpt-5.2"},
        _make_turn("q1", "a1", now - 100),
    ]
    frame.view_mode = "active"

    frame._render_answer_list()

    kinds = [meta[0] for meta in frame.answer_meta]
    assert kinds == [
        "user", "question", "ai", "answer",
        "time", "user", "question", "ai", "answer",
    ]


def test_incremental_appends_insert_time_row_once(frame):
    now = time.time()
    turn0 = _make_turn("q0", "", now - 1000)
    frame.active_session_turns = [turn0]
    frame.view_mode = "active"

    assert frame._append_submitted_question_to_answer_list(0, turn0) is True

    kinds = [meta[0] for meta in frame.answer_meta]
    assert kinds == ["time", "user", "question"]

    # 回答完成时不再重复插入时间行
    turn0["answer_md"] = "a0"
    assert frame._append_completed_answer_to_answer_list(0, turn0) is True
    kinds = [meta[0] for meta in frame.answer_meta]
    assert kinds == ["time", "user", "question", "ai", "answer"]

    # 间隔 200 秒内的下一条消息不再显示时间行
    turn1 = _make_turn("q1", "", now - 800)
    frame.active_session_turns.append(turn1)
    assert frame._append_submitted_question_to_answer_list(1, turn1) is True
    kinds = [meta[0] for meta in frame.answer_meta]
    assert kinds == ["time", "user", "question", "ai", "answer", "user", "question"]
