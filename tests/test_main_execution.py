import copy
import main
import pytest
import threading
import time
import wx
from main_test_support import (
    _execution_content_ids,
    _execution_content_labels,
    _execution_row_index,
)
from types import SimpleNamespace


@pytest.mark.parametrize(
    "times,separator_indexes",
    [
        ([0, 200, 299.999, 300, 599.999, 600], [0, 3, 5]),
        ([1000, 1299.999, 1300], [0, 2]),
    ],
)
def test_execution_time_groups_use_last_displayed_anchor(frame, times, separator_indexes):
    frame.current_chat_id = frame.active_chat_id = "grouping"
    frame._current_chat_state = {
        "id": "grouping", "revision": 7, "turns": [], "detail_panel_mode": "execution",
        "execution_steps": [
            {"step": str(index), "created_at": timestamp, "_execution_uid": f"event-{index}", "revision": 7}
            for index, timestamp in enumerate(times)
        ],
    }
    rows, metas = frame._execution_page_projection()
    assert [meta[1] for meta in metas if meta[0] == "time"] == separator_indexes
    assert sum(meta[0] != "time" for meta in metas) == len(times)
    assert all("revision:7" in row_id for (row_id, _), meta in zip(rows, metas) if meta[0] == "time")


def test_unknown_legacy_execution_separator_identity_survives_prepend(frame):
    frame.current_chat_id = frame.active_chat_id = "legacy"
    legacy = {"step": "unknown", "created_at": None}
    frame._current_chat_state = {
        "id": "legacy", "revision": 2, "turns": [], "detail_panel_mode": "execution", "execution_steps": [legacy],
    }
    first_rows, first_meta = frame._execution_page_projection()
    first_time_id = next(row[0] for row, meta in zip(first_rows, first_meta) if meta[0] == "time")
    assert "legacy-quarantine" in first_time_id
    frame._current_chat_state["execution_steps"].insert(
        0, {"step": "older", "created_at": None, "_execution_uid": "older", "revision": 2}
    )
    second_rows, second_meta = frame._execution_page_projection()
    unknown_index = next(i for i, meta in enumerate(second_meta) if meta[0] != "time" and meta[2] == "unknown")
    assert second_rows[unknown_index - 1][0] == first_time_id


def test_legacy_native_identity_uses_complete_story32_scope(frame):
    frame.current_chat_id = frame.active_chat_id = "scope"
    frame._current_chat_state = {
        "id": "scope", "revision": 3, "turns": [], "detail_panel_mode": "execution",
        "execution_steps": [
            {"step": "a", "item_id": "reused", "turn_id": "turn-a", "provider": "codex", "created_at": None},
            {"step": "b", "item_id": "reused", "turn_id": "turn-b", "provider": "codex", "created_at": None},
            {"step": "c", "item_id": "reused", "turn_id": "turn-a", "provider": "kimi", "created_at": None},
        ],
    }
    rows, metas = frame._execution_page_projection()
    content_ids = [row[0] for row, meta in zip(rows, metas) if meta[0] != "time"]
    time_ids = [row[0] for row, meta in zip(rows, metas) if meta[0] == "time"]
    assert len(set(content_ids)) == 3
    assert len(set(time_ids)) == 3


def test_identityless_legacy_ids_survive_deepcopy_reload_and_prepend(frame):
    duplicate = {"step": "same", "detail_text": "immutable legacy payload", "created_at": None}
    timeline = [copy.deepcopy(duplicate), copy.deepcopy(duplicate)]
    frame.current_chat_id = frame.active_chat_id = "copied"
    frame._current_chat_state = {
        "id": "copied", "revision": 4, "turns": [], "detail_panel_mode": "execution", "execution_steps": timeline,
    }
    first_rows, first_meta = frame._execution_page_projection()
    first_content = [row[0] for row, meta in zip(first_rows, first_meta) if meta[0] != "time"]
    first_times = [row[0] for row, meta in zip(first_rows, first_meta) if meta[0] == "time"]
    assert len(set(first_content)) == len(set(first_times)) == 2
    frame._current_chat_state["execution_steps"] = [
        {"step": "older", "detail_text": "different immutable payload", "created_at": None},
        *copy.deepcopy(timeline),
    ]
    second_rows, second_meta = frame._execution_page_projection()
    second_content = [row[0] for row, meta in zip(second_rows, second_meta) if meta[0] != "time"][1:]
    second_times = [row[0] for row, meta in zip(second_rows, second_meta) if meta[0] == "time"][1:]
    assert second_content == first_content
    assert second_times == first_times


def test_late_identical_identityless_insertion_is_blocked(frame):
    duplicate = {"step": "same", "detail_text": "indistinguishable", "created_at": None}
    frame.current_chat_id = frame.active_chat_id = "late-ambiguous"
    frame._current_chat_state = {
        "id": "late-ambiguous", "revision": 1, "turns": [], "detail_panel_mode": "execution",
        "execution_steps": [copy.deepcopy(duplicate)],
    }
    frame._execution_page_projection()
    frame._current_chat_state["execution_steps"].append(copy.deepcopy(duplicate))
    with pytest.raises(main.ExecutionIdentityBlocked, match="ambiguous insertion"):
        frame._execution_page_projection()


def test_mixed_middle_identical_insertion_blocks_without_native_replacement(frame):
    duplicate = {"step": "D", "detail_text": "same D", "created_at": None}
    other = {"step": "X", "detail_text": "different X", "created_at": None}
    frame.current_chat_id = frame.active_chat_id = "mixed-ambiguous"
    frame._current_chat_state = {
        "id": "mixed-ambiguous", "revision": 1, "turns": [], "detail_panel_mode": "execution",
        "execution_steps": [copy.deepcopy(duplicate), copy.deepcopy(other), copy.deepcopy(duplicate)],
    }
    frame._render_execution_list(force=True)
    first_time = next(i for i, meta in enumerate(frame.execution_meta) if meta[0] == "time")
    frame.execution_list.SetSelection(first_time)
    frame._acquire_execution_focus_lease()
    prior_ids = list(frame.execution_list_model.visible_ids)
    prior_labels = list(frame.execution_list.GetStrings())
    selected_id = frame.execution_list_model.selected_id()
    frame._current_chat_state["execution_steps"] = [
        copy.deepcopy(duplicate), copy.deepcopy(duplicate), copy.deepcopy(other), copy.deepcopy(duplicate),
    ]
    frame._render_execution_list(force=True)
    assert len(frame._current_chat_state["execution_steps"]) == 4
    assert frame.execution_list_model.visible_ids == prior_ids
    assert list(frame.execution_list.GetStrings()) == prior_labels
    assert frame.execution_list_model.selected_id() == selected_id


@pytest.mark.parametrize(
    "initial,replacement",
    [
        (["D", "X"], ["D", "D"]),
        (["D", "X", "Y"], ["D", "D"]),
    ],
)
def test_identityless_duplicate_count_increase_blocks_even_without_total_growth(frame, initial, replacement):
    def row(value):
        return {"step": value, "detail_text": f"immutable {value}", "created_at": None}

    frame.current_chat_id = frame.active_chat_id = "count-ambiguity"
    frame._current_chat_state = {
        "id": "count-ambiguity", "revision": 1, "turns": [], "detail_panel_mode": "execution",
        "execution_steps": [row(value) for value in initial],
    }
    frame._render_execution_list(force=True)
    frame.execution_list.SetSelection(0)
    prior_ids = list(frame.execution_list_model.visible_ids)
    prior_labels = list(frame.execution_list.GetStrings())
    selected_id = frame.execution_list_model.selected_id()
    frame._current_chat_state["execution_steps"] = [row(value) for value in replacement]
    frame._render_execution_list(force=True)
    assert frame.execution_list_model.visible_ids == prior_ids
    assert list(frame.execution_list.GetStrings()) == prior_labels
    assert frame.execution_list_model.selected_id() == selected_id


def test_build_execution_entry_keeps_full_detail_text(frame):
    event = main.CodexEvent(
        type="item_started",
        thread_id="thread-current",
        turn_id="turn-current",
        item_id="item-1",
        subtype="commandExecution",
        title="运行测试",
        command="pytest tests/test_main_unit.py -k codex",
        raw_text="运行测试\npytest tests/test_main_unit.py -k codex\n第 2 行输出",
    )

    entry = frame._build_execution_entry(event)

    assert entry is not None
    assert entry["event_type"] == "item_started"
    assert entry["display_kind"] == "command"
    assert entry["thread_id"] == "thread-current"
    assert entry["turn_id"] == "turn-current"
    assert entry["item_id"] == "item-1"
    assert entry["detail_text"] == "运行测试\npytest tests/test_main_unit.py -k codex\n第 2 行输出"
    assert entry["list_text"] == "命令：开始执行 运行测试 pytest tests/test_main_unit.py -k codex"
    assert "\n" not in entry["list_text"]
    assert "第 2 行输出" not in entry["list_text"]


def test_build_execution_entry_command_summary_falls_back_to_stable_fields(frame):
    event = main.CodexEvent(
        type="item_started",
        thread_id="thread-current",
        turn_id="turn-current",
        item_id="item-2",
        subtype="commandExecution",
        raw_text="第一行输出\n第二行输出",
    )

    entry = frame._build_execution_entry(event)

    assert entry is not None
    assert entry["display_kind"] == "command"
    assert entry["detail_text"] == "第一行输出\n第二行输出"
    assert entry["list_text"] == "命令：开始执行 commandExecution"
    assert "第一行输出" not in entry["list_text"]


def test_unresolved_codex_event_does_not_append_to_idle_visible_execution_chat(frame, monkeypatch):
    frame.active_chat_id = "chat-a"
    frame.current_chat_id = "chat-a"
    frame.active_codex_turn_active = False
    frame._current_chat_state = {
        "id": "chat-a",
        "title": "聊天 A",
        "turns": [{"question": "A 的问题", "answer_md": "A 的回答", "model": main.DEFAULT_CODEX_MODEL}],
        "detail_panel_mode": "execution",
        "execution_steps": [{"step": "A 原有过程"}],
        "codex_turn_active": False,
    }
    frame.archived_chats = [
        {
            "id": "chat-b",
            "title": "聊天 B",
            "turns": [
                {
                    "question": "B 的问题",
                    "answer_md": main.REQUESTING_TEXT,
                    "model": main.DEFAULT_CODEX_MODEL,
                    "request_status": "pending",
                }
            ],
            "detail_panel_mode": "execution",
            "execution_steps": [],
            "codex_turn_active": True,
        }
    ]
    frame._render_execution_list()
    before_rows = [frame.execution_list.GetString(i) for i in range(frame.execution_list.GetCount())]
    monkeypatch.setattr(frame, "_save_state", lambda: None)

    frame._on_codex_event(
        main.CodexEvent(
            type="plan_updated",
            text="B 的计划不应写入 A",
        )
    )

    assert frame._current_chat_state["execution_steps"] == [{"step": "A 原有过程"}]
    assert frame.archived_chats[0]["execution_steps"] == []
    assert [frame.execution_list.GetString(i) for i in range(frame.execution_list.GetCount())] == before_rows


def test_plan_updated_appends_execution_step(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_codex_thread_id = "thread-current"
    frame.active_codex_turn_id = "turn-current"
    frame.active_session_turns = [{"question": "q", "answer_md": "", "model": main.DEFAULT_CODEX_MODEL, "created_at": 1.0}]
    frame._current_chat_state = {
        "id": "chat-current",
        "title": "当前聊天",
        "turns": frame.active_session_turns,
        "detail_panel_mode": "answers",
        "execution_steps": [],
    }
    monkeypatch.setattr(frame, "_save_state", lambda: None)

    frame._on_codex_event_for_chat(
        "chat-current",
        main.CodexEvent(type="plan_updated", thread_id="thread-current", turn_id="turn-current", text="先整理结构"),
    )

    steps = frame._current_chat_state["execution_steps"]
    assert len(steps) == 1
    assert steps[0]["event_type"] == "plan_updated"
    assert steps[0]["display_kind"] == "plan"
    assert steps[0]["detail_text"] == "先整理结构"
    assert steps[0]["list_text"] == "计划：先整理结构"


def test_command_execution_started_appends_detailed_execution_step(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_codex_thread_id = "thread-current"
    frame.active_codex_turn_id = "turn-current"
    frame.active_session_turns = [{"question": "q", "answer_md": "", "model": main.DEFAULT_CODEX_MODEL, "created_at": 1.0}]
    frame._current_chat_state = {
        "id": "chat-current",
        "title": "当前聊天",
        "turns": frame.active_session_turns,
        "detail_panel_mode": "execution",
        "execution_steps": [],
    }
    monkeypatch.setattr(frame, "_save_state", lambda: None)

    frame._on_codex_event_for_chat(
        "chat-current",
        main.CodexEvent(
            type="item_started",
            thread_id="thread-current",
            turn_id="turn-current",
            status="commandExecution",
            display_kind="command",
            data={
                "type": "commandExecution",
                "title": "运行测试",
                "command": "pytest tests/test_main_unit.py -k codex",
            },
        ),
    )

    steps = frame._current_chat_state["execution_steps"]
    assert len(steps) == 1
    assert steps[0]["event_type"] == "item_started"
    assert steps[0]["display_kind"] == "command"
    assert steps[0]["command"] == "pytest tests/test_main_unit.py -k codex"
    assert steps[0]["detail_text"] == "开始执行：运行测试\n命令：pytest tests/test_main_unit.py -k codex"
    assert steps[0]["list_text"] == "命令：开始执行 运行测试 pytest tests/test_main_unit.py -k codex"


def test_file_change_completed_appends_changed_paths_to_execution_step(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_codex_thread_id = "thread-current"
    frame.active_codex_turn_id = "turn-current"
    frame.active_session_turns = [{"question": "q", "answer_md": "", "model": main.DEFAULT_CODEX_MODEL, "created_at": 1.0}]
    frame._current_chat_state = {
        "id": "chat-current",
        "title": "当前聊天",
        "turns": frame.active_session_turns,
        "detail_panel_mode": "execution",
        "execution_steps": [],
    }
    monkeypatch.setattr(frame, "_save_state", lambda: None)

    frame._on_codex_event_for_chat(
        "chat-current",
        main.CodexEvent(
            type="item_completed",
            thread_id="thread-current",
            turn_id="turn-current",
            status="fileChange",
            data={
                "type": "fileChange",
                "changes": [
                    {"path": "main.py"},
                    {"path": "tests/test_main_unit.py"},
                ],
            },
        ),
    )

    steps = frame._current_chat_state["execution_steps"]
    assert len(steps) == 1
    assert steps[0]["event_type"] == "item_completed"
    assert steps[0]["detail_text"] == "完成执行：修改文件 main.py, tests/test_main_unit.py"
    assert steps[0]["list_text"] == "完成执行：修改文件 main.py, tests/test_main_unit.py"


def test_empty_text_mapped_event_appends_and_persists_execution_step(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_codex_thread_id = "thread-current"
    frame.active_codex_turn_id = "turn-current"
    frame.active_session_turns = [{"question": "q", "answer_md": "", "model": main.DEFAULT_CODEX_MODEL, "created_at": 1.0}]
    frame._current_chat_state = {
        "id": "chat-current",
        "title": "当前聊天",
        "turns": frame.active_session_turns,
        "detail_panel_mode": "answers",
        "execution_steps": [],
    }
    saves = {"count": 0}
    monkeypatch.setattr(frame, "_save_state", lambda: saves.__setitem__("count", saves["count"] + 1))

    frame._on_codex_event_for_chat(
        "chat-current",
        main.CodexEvent(type="diff_updated", thread_id="thread-current", turn_id="turn-current", text=""),
    )

    steps = frame._current_chat_state["execution_steps"]
    assert len(steps) == 1
    assert steps[0]["event_type"] == "diff_updated"
    assert steps[0]["detail_text"] == "已生成代码变更"
    assert steps[0]["list_text"] == "已生成代码变更"
    assert saves["count"] == 0
    assert frame._codex_background_flush_scheduled is True


def test_agent_message_delta_buffers_without_immediate_execution_step(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_codex_thread_id = "thread-current"
    frame.active_codex_turn_id = "turn-current"
    frame.active_session_turns = [{"question": "q", "answer_md": "", "model": main.DEFAULT_CODEX_MODEL, "created_at": 1.0}]
    frame._current_chat_state = {
        "id": "chat-current",
        "title": "当前聊天",
        "turns": frame.active_session_turns,
        "detail_panel_mode": "answers",
        "execution_steps": [],
    }
    monkeypatch.setattr(frame, "_save_state", lambda: None)

    frame._on_codex_event_for_chat(
        "chat-current",
        main.CodexEvent(type="agent_message_delta", thread_id="thread-current", turn_id="turn-current", item_id="msg-1", fragment_id="f1", text="stream"),
    )

    assert frame._current_chat_state["execution_steps"] == []
    buffered = frame._execution_delta_buffer[("chat-current", "turn-current", "msg-1", "", "", "")]
    assert buffered["parts"] == ["stream"]
    assert buffered["event"].type == "agent_message_delta"
    assert buffered["event"].item_id == "msg-1"
    assert frame.active_session_turns[0]["answer_md"] == main.REQUESTING_TEXT


def test_execution_delta_buffer_flushes_into_single_commentary_item(frame, monkeypatch):
    frame.active_chat_id = "chat-1"
    frame.current_chat_id = "chat-1"
    frame._current_chat_state = {
        "id": "chat-1",
        "title": "当前聊天",
        "turns": [],
        "detail_panel_mode": "execution",
        "execution_steps": [],
    }
    monkeypatch.setattr(frame, "_save_state", lambda: None)

    frame._buffer_execution_delta(
        "chat-1",
        main.CodexEvent(type="agent_message_delta", thread_id="thread-1", turn_id="turn-1", item_id="msg-1", fragment_id="f1", text="先检查 main.py。"),
    )
    frame._buffer_execution_delta(
        "chat-1",
        main.CodexEvent(type="agent_message_delta", thread_id="thread-1", turn_id="turn-1", item_id="msg-1", fragment_id="f2", text="再处理 codex_client.py。"),
    )

    flushed = frame._flush_execution_delta("chat-1", "turn-1", "msg-1")

    assert flushed is True
    assert frame._execution_delta_buffer == {}
    assert len(frame._current_chat_state["execution_steps"]) == 1
    assert frame._current_chat_state["execution_steps"][0]["event_type"] == "agent_message_delta"
    assert frame._current_chat_state["execution_steps"][0]["display_kind"] == "commentary"
    assert frame._current_chat_state["execution_steps"][0]["detail_text"] == "先检查 main.py。再处理 codex_client.py。"


@pytest.mark.parametrize("provider_origin,expected", [(1_795_000_000.0, 1_795_000_000.0), (None, 1_800_000_000.0)])
def test_execution_delta_delayed_flush_retains_first_origin_or_fallback(
    frame, monkeypatch, provider_origin, expected
):
    frame.active_chat_id = frame.current_chat_id = "origin"
    frame._current_chat_state = {"id": "origin", "turns": [], "execution_steps": []}
    frame._chat_store_enabled = False
    clock = iter([1_800_000_000.0, 1_800_000_100.0, 1_800_000_200.0])
    monkeypatch.setattr(main.time, "time", lambda: next(clock))
    common = dict(type="agent_message_delta", thread_id="thread", turn_id="turn", item_id="item")
    frame._buffer_execution_delta("origin", main.CodexEvent(
        **common, fragment_id="one", text="first", origin_timestamp=provider_origin
    ))
    frame._buffer_execution_delta("origin", main.CodexEvent(
        **common, fragment_id="two", text=" second", origin_timestamp=1_795_000_999.0
    ))
    assert frame._flush_execution_delta("origin", "turn", "item") is True
    assert frame._current_chat_state["execution_steps"][0]["created_at"] == expected


def test_execution_delta_flush_preserves_fragment_edge_whitespace(frame, monkeypatch):
    frame.active_chat_id = frame.current_chat_id = "chat-1"
    frame._current_chat_state = {"id": "chat-1", "turns": [], "execution_steps": []}
    frame._chat_store_enabled = False
    captured = []
    monkeypatch.setattr(frame, "_build_execution_entry", lambda event: captured.append(event) or None)

    frame._buffer_execution_delta(
        "chat-1",
        main.CodexEvent(type="agent_message_delta", thread_id="thread-1", turn_id="turn-1", item_id="edge-message-1", fragment_id="edge-f1", text=" leading"),
    )
    frame._buffer_execution_delta(
        "chat-1",
        main.CodexEvent(type="agent_message_delta", thread_id="thread-1", turn_id="turn-1", item_id="edge-message-1", fragment_id="edge-f2", text=" trailing "),
    )

    assert frame._flush_execution_delta("chat-1", "turn-1") is False
    assert len(captured) == 1
    assert captured[0].text == " leading trailing "
    assert captured[0].raw_text == " leading trailing "


def test_kimi_execution_delta_buffer_separates_agent_and_source_streams(frame):
    frame.active_chat_id = "chat-1"
    frame.current_chat_id = "chat-1"
    frame._current_chat_state = {"id": "chat-1", "turns": [], "execution_steps": []}
    for text, kind, agent, source in (
        ("think-a", "thinking", "agent-a", "thinking.delta"),
        ("think-b", "thinking", "agent-b", "thinking.delta"),
        ("tool-a", "commentary", "agent-a", "tool.progress"),
    ):
        frame._buffer_execution_delta(
            "chat-1",
            main.CodexEvent(
                type="agent_message_delta",
                    thread_id="thread-1",
                    turn_id="turn-1",
                    item_id=f"{agent}:{source}",
                    fragment_id=f"fragment:{agent}:{source}",
                text=text,
                display_kind=kind,
                    data={"adapter": "kimi_server", "agent_id": agent, "source_kind": source},
            ),
        )

    assert len(frame._execution_delta_buffer) == 3
    assert frame._flush_execution_delta("chat-1", "turn-1") is True
    assert [step["list_text"] for step in frame._current_chat_state["execution_steps"]] == ["正在分析问题", "正在分析问题"]


def test_non_kimi_item_title_keeps_existing_execution_summary_behavior(frame):
    event = main.CodexEvent(type="item_started", title="Provider title")

    assert frame._execution_detail_text_from_event(event) == "开始执行：步骤"


def test_append_execution_entry_to_chat_keeps_adjacent_identical_commentary_without_identity(frame, monkeypatch):
    frame.active_chat_id = "chat-1"
    frame.current_chat_id = "chat-1"
    frame._current_chat_state = {
        "id": "chat-1",
        "title": "当前聊天",
        "turns": [],
        "detail_panel_mode": "execution",
        "execution_steps": [
            {
                "event_type": "agent_message_delta",
                "display_kind": "commentary",
                "detail_text": "我先核对当前会话里注册的 skill 定义。",
                "list_text": "我先核对当前会话里注册的 skill 定义。",
            }
        ],
    }
    monkeypatch.setattr(frame, "_save_state", lambda: None)

    appended = frame._append_execution_entry_to_chat(
        "chat-1",
        {
            "event_type": "item_completed",
            "display_kind": "commentary",
            "detail_text": "我先核对当前会话里注册的 skill 定义。",
            "list_text": "我先核对当前会话里注册的 skill 定义。",
        },
        save_state=False,
    )

    assert appended is True
    assert len(frame._current_chat_state["execution_steps"]) == 2


def test_append_execution_entry_to_chat_keeps_adjacent_expanded_commentary_without_identity(frame, monkeypatch):
    frame.active_chat_id = "chat-1"
    frame.current_chat_id = "chat-1"
    frame._current_chat_state = {
        "id": "chat-1",
        "title": "当前聊天",
        "turns": [],
        "detail_panel_mode": "execution",
        "execution_steps": [
            {
                "event_type": "agent_message_delta",
                "display_kind": "commentary",
                "detail_text": "我先核对当前会话里注册的 skill 定义和对应 SKILL.md 是否都在本地可读。",
                "list_text": "我先核对当前会话里注册的 skill 定义和对应 SKILL.md 是否都在本地可读。",
            }
        ],
    }
    monkeypatch.setattr(frame, "_save_state", lambda: None)

    appended = frame._append_execution_entry_to_chat(
        "chat-1",
        {
            "event_type": "item_completed",
            "display_kind": "commentary",
            "detail_text": "我先核对当前会话里注册的 skill 定义和对应 SKILL.md 是否都在本地可读。再给你一个明确结论。",
            "list_text": "我先核对当前会话里注册的 skill 定义和对应 SKILL.md 是否都在本地可读。再给你一个明确结论。",
        },
        save_state=False,
    )

    assert appended is True
    assert len(frame._current_chat_state["execution_steps"]) == 2


def test_append_execution_entry_broadcasts_remote_execution_entry(frame, monkeypatch):
    events = []
    monkeypatch.setattr(frame, "_broadcast_remote_event", lambda payload: events.append(payload))
    frame.active_chat_id = "chat-1"
    frame.current_chat_id = "chat-1"
    frame.active_turn_idx = 0
    frame._current_chat_state = {
        "id": "chat-1",
        "turns": [{"question": "q", "answer_md": "", "model": main.DEFAULT_CODEX_MODEL}],
        "execution_steps": [],
    }

    appended = frame._append_execution_entry_to_chat(
        "chat-1",
        {"event_type": "plan_updated", "list_text": "计划：整理", "detail_text": "整理步骤"},
        save_state=False,
    )

    assert appended is True
    assert len(events) == 1
    assert events[0]["type"] == "execution_entry"
    assert events[0]["chat_id"] == "chat-1"
    assert events[0]["kind"] == "plan_updated"
    assert events[0]["title"] == "计划：整理"
    assert events[0]["detail"] == "整理步骤"
    assert events[0]["ts"]
    assert events[0]["event_id"]


def test_identityless_commentary_entry_broadcasts_remote_event(frame, monkeypatch):
    events = []
    monkeypatch.setattr(frame, "_broadcast_remote_event", lambda payload: events.append(payload))
    frame.active_chat_id = "chat-1"
    frame.current_chat_id = "chat-1"
    frame.active_turn_idx = 0
    frame._current_chat_state = {
        "id": "chat-1",
        "turns": [{"question": "q", "answer_md": "", "model": main.DEFAULT_CODEX_MODEL}],
        "execution_steps": [
            {
                "event_type": "agent_message_delta",
                "display_kind": "commentary",
                "list_text": "我先核对当前会话里注册的 skill 定义。",
                "detail_text": "我先核对当前会话里注册的 skill 定义。",
            }
        ],
    }

    appended = frame._append_execution_entry_to_chat(
        "chat-1",
        {
            "event_type": "item_completed",
            "display_kind": "commentary",
            "list_text": "我先核对当前会话里注册的 skill 定义。",
            "detail_text": "我先核对当前会话里注册的 skill 定义。",
        },
        save_state=False,
    )

    assert appended is True
    assert len(events) == 1
    assert events[0]["type"] == "execution_entry"
    assert events[0]["canonical_item_id"]


def test_execution_entry_replay_dedupes_by_stable_provider_identity(frame, monkeypatch):
    events = []
    monkeypatch.setattr(frame, "_broadcast_remote_event", lambda payload: events.append(payload))
    frame.active_chat_id = "chat-1"
    frame.current_chat_id = "chat-1"
    frame.active_turn_idx = 0
    frame._current_chat_state = {
        "id": "chat-1",
        "title": "当前聊天",
        "turns": [{"question": "q", "answer_md": "", "model": main.DEFAULT_CODEX_MODEL}],
        "execution_steps": [],
    }
    entry = {
        "event_type": "agent_message_delta",
        "display_kind": "commentary",
        "detail_text": "正在核对稳定事件身份。",
        "list_text": "正在核对稳定事件身份。",
        "session_id": "thread-1",
        "thread_id": "thread-1",
        "turn_id": "turn-1",
        "provider": "codex",
        "item_id": "provider-item-1",
    }

    first = frame._append_execution_entry_to_chat("chat-1", dict(entry), save_state=False)
    replay = frame._append_execution_entry_to_chat("chat-1", dict(entry), save_state=False)

    assert first is True
    assert replay is False
    assert len(frame._current_chat_state["execution_steps"]) == 1
    assert len(events) == 1
    assert events[0]["canonical_item_id"] == frame._current_chat_state["execution_steps"][0]["canonical_item_id"]


def test_identical_commentary_from_distinct_provider_items_remains_distinct(frame, monkeypatch):
    events = []
    monkeypatch.setattr(frame, "_broadcast_remote_event", lambda payload: events.append(payload))
    frame.active_chat_id = "chat-1"
    frame.current_chat_id = "chat-1"
    frame.active_turn_idx = 0
    frame._current_chat_state = {
        "id": "chat-1",
        "title": "当前聊天",
        "turns": [{"question": "q", "answer_md": "", "model": main.DEFAULT_CODEX_MODEL}],
        "execution_steps": [],
    }
    base_entry = {
        "event_type": "agent_message_delta",
        "display_kind": "commentary",
        "detail_text": "我正在检查稳定事件身份。",
        "list_text": "我正在检查稳定事件身份。",
        "session_id": "thread-1",
        "thread_id": "thread-1",
        "turn_id": "turn-1",
        "provider": "codex",
    }

    first = frame._append_execution_entry_to_chat(
        "chat-1", dict(base_entry, item_id="provider-item-1"), save_state=False
    )
    second = frame._append_execution_entry_to_chat(
        "chat-1", dict(base_entry, item_id="provider-item-2"), save_state=False
    )

    steps = frame._current_chat_state["execution_steps"]
    assert first is True
    assert second is True
    assert len(steps) == 2
    assert len(events) == 2
    assert len({step["logical_key"] for step in steps}) == 2
    assert len({event["canonical_item_id"] for event in events}) == 2


def test_identical_non_commentary_execution_entry_still_appends(frame, monkeypatch):
    events = []
    monkeypatch.setattr(frame, "_broadcast_remote_event", lambda payload: events.append(payload))
    frame.active_chat_id = "chat-1"
    frame.current_chat_id = "chat-1"
    frame.active_turn_idx = 0
    frame._current_chat_state = {
        "id": "chat-1",
        "turns": [{"question": "q", "answer_md": "", "model": main.DEFAULT_CODEX_MODEL}],
        "execution_steps": [
            {
                "event_type": "plan_updated",
                "display_kind": "plan",
                "list_text": "计划：整理",
                "detail_text": "整理步骤",
            }
        ],
    }

    appended = frame._append_execution_entry_to_chat(
        "chat-1",
        {
            "event_type": "plan_updated",
            "display_kind": "plan",
            "list_text": "计划：整理",
            "detail_text": "整理步骤",
        },
        save_state=False,
    )

    assert appended is True
    assert len(frame._current_chat_state["execution_steps"]) == 2
    assert len(events) == 1


def test_final_answer_item_appends_execution_step_without_breaking_answer_update(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_codex_thread_id = "thread-current"
    frame.active_codex_turn_id = "turn-current"
    frame.active_turn_idx = 0
    frame.active_session_turns = [{"question": "q", "answer_md": "", "model": main.DEFAULT_CODEX_MODEL, "created_at": 1.0}]
    frame._current_chat_state = {
        "id": "chat-current",
        "title": "当前聊天",
        "turns": frame.active_session_turns,
        "detail_panel_mode": "answers",
        "execution_steps": [],
    }
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_push_remote_final_answer", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_render_answer_list", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_call_later_if_alive", lambda *args, **kwargs: None)

    frame._on_codex_event_for_chat(
        "chat-current",
        main.CodexEvent(type="item_completed", phase="final_answer", thread_id="thread-current", turn_id="turn-current", text="最终回答"),
    )

    assert frame.active_session_turns[0]["answer_md"] == "最终回答"
    steps = frame._current_chat_state["execution_steps"]
    assert len(steps) == 1
    assert steps[0]["event_type"] == "item_completed"
    assert steps[0]["phase"] == "final_answer"
    assert steps[0]["detail_text"] == "最终回答"
    assert steps[0]["list_text"] == "已生成最终回答"


def test_background_non_current_chat_events_append_to_target_chat_execution_steps(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame._current_chat_state = {
        "id": "chat-current",
        "title": "当前聊天",
        "turns": [],
        "detail_panel_mode": "answers",
        "execution_steps": [],
    }
    frame.archived_chats = [
        {
            "id": "chat-background",
            "title": "后台聊天",
            "turns": [],
            "created_at": 1.0,
            "updated_at": 1.0,
            "detail_panel_mode": "answers",
            "execution_steps": [],
            "codex_thread_id": "thread-background",
            "codex_turn_id": "turn-background",
        }
    ]
    monkeypatch.setattr(frame, "_save_state", lambda: None)

    frame._on_codex_event_for_chat(
        "chat-background",
        main.CodexEvent(type="plan_updated", thread_id="thread-background", turn_id="turn-background", text="后台整理"),
    )

    archived = frame._find_archived_chat("chat-background")
    assert len(archived["execution_steps"]) == 1
    assert archived["execution_steps"][0]["event_type"] == "plan_updated"
    assert archived["execution_steps"][0]["detail_text"] == "后台整理"
    assert archived["execution_steps"][0]["list_text"] == "计划：后台整理"
    assert frame._current_chat_state["execution_steps"] == []


def test_execution_shift_enter_keeps_web_detail_shortcut(frame, monkeypatch):
    frame._current_chat_state["detail_panel_mode"] = "execution"
    frame.execution_list.Set(["执行步骤"])
    frame.execution_meta = [("execution", 0, "执行步骤", "详情")]
    frame.execution_list.SetSelection(0)
    opened_viewer = []
    opened_web = []
    monkeypatch.setattr(frame, "_open_selected_execution_text_viewer", lambda: opened_viewer.append(True) or True)
    monkeypatch.setattr(frame, "_try_open_selected_execution_detail", lambda: opened_web.append(True) or True)

    class E:
        def GetKeyCode(self):
            return wx.WXK_RETURN

        def ControlDown(self):
            return False

        def AltDown(self):
            return False

        def ShiftDown(self):
            return True

        def Skip(self):
            raise AssertionError("Shift+Enter should be handled")

    frame._on_execution_key_down(E())

    assert opened_viewer == []
    assert opened_web == [True]


def test_save_state_does_not_rewrite_incrementally_persisted_execution_steps(frame, tmp_path, monkeypatch):
    frame.state_path = tmp_path / "app_state.json"
    frame.chat_db_path = tmp_path / "chat_history.db"
    frame.chat_store = main.ChatStore(frame.chat_db_path)
    frame.chat_store.initialize()
    frame._chat_store_enabled = True
    frame.active_chat_id = "chat-active"
    frame.current_chat_id = "chat-active"
    frame.active_session_turns = [{"question": "active q", "answer_md": "active a", "model": "codex/main"}]
    step = {"turn_idx": 0, "display_kind": "plan", "list_text": "already persisted", "detail_text": "already persisted"}
    frame._current_chat_state = {
        "id": "chat-active",
        "title": "active",
        "turns": frame.active_session_turns,
        "execution_steps": [step],
    }
    frame.chat_store.upsert_chat(frame._current_chat_state)
    frame.chat_store.replace_turns("chat-active", frame.active_session_turns)
    frame.chat_store.append_execution_step("chat-active", step)
    calls = []
    monkeypatch.setattr(frame.chat_store, "replace_execution_steps", lambda *args, **kwargs: calls.append((args, kwargs)))

    frame._save_state()

    assert calls == []
    assert [item["list_text"] for item in frame.chat_store.load_execution_steps("chat-active")] == ["already persisted"]
    assert frame.chat_store.load_turns("chat-active") == frame.active_session_turns


def test_append_execution_entry_prunes_cached_steps_per_turn(frame, monkeypatch):
    frame.active_chat_id = "chat-active"
    frame.current_chat_id = "chat-active"
    frame.active_turn_idx = 0
    frame._current_chat_state = {
        "id": "chat-active",
        "turns": [{"question": "q", "answer_md": ""}],
        "execution_steps": [
            {"turn_idx": 0, "list_text": "step 0"},
            {"turn_idx": 0, "list_text": "step 1"},
            {"turn_idx": 0, "list_text": "step 2"},
            {"turn_idx": 1, "list_text": "other turn"},
        ],
    }
    persisted = []
    frame.chat_store = SimpleNamespace(
        max_execution_steps_per_turn=3,
        append_execution_step=lambda _chat_id, step: persisted.append(copy.deepcopy(step)),
    )
    frame._chat_store_enabled = True
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_broadcast_remote_event", lambda *args, **kwargs: None)

    assert frame._append_execution_entry_to_chat("chat-active", {"turn_idx": 0, "list_text": "step 3"})

    assert [step["list_text"] for step in frame._current_chat_state["execution_steps"] if step.get("turn_idx") == 0] == [
        "step 1",
        "step 2",
        "step 3",
    ]
    assert [step["list_text"] for step in frame._current_chat_state["execution_steps"] if step.get("turn_idx") == 1] == [
        "other turn",
    ]
    assert persisted[-1]["list_text"] == "step 3"


def test_codex_batch_queues_execution_step_persistence_off_ui_thread(frame, monkeypatch):
    frame.active_chat_id = "chat-active"
    frame.current_chat_id = "chat-active"
    frame.active_turn_idx = 0
    frame._current_chat_state = {"id": "chat-active", "turns": [{"question": "q"}], "execution_steps": []}
    persisted = []
    frame.chat_store = SimpleNamespace(
        max_execution_steps_per_turn=1000,
        append_execution_step=lambda chat_id, step: persisted.append((chat_id, copy.deepcopy(step))),
    )
    frame._chat_store_enabled = True
    frame._codex_ui_batch_depth = 1
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_broadcast_remote_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_call_later_if_alive", lambda *args, **kwargs: object())

    assert frame._append_execution_entry_to_chat("chat-active", {"turn_idx": 0, "list_text": "step 1"})

    assert persisted == []
    assert frame._pending_execution_step_persists


def test_execution_step_persist_flush_waits_for_running_worker(frame):
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    persisted = []

    def append_execution_step(chat_id, step):
        entered.set()
        assert release.wait(timeout=2.0)
        persisted.append((chat_id, copy.deepcopy(step)))
        finished.set()

    frame.chat_store = SimpleNamespace(append_execution_step=append_execution_step)
    frame._chat_store_enabled = True
    frame._codex_ui_batch_depth = 1
    frame._queue_execution_step_persist("chat-active", {"list_text": "step 1"})
    frame._codex_ui_batch_depth = 0
    frame._start_execution_step_persist_worker()
    assert entered.wait(timeout=2.0)

    threading.Timer(0.05, release.set).start()
    frame._closing = True
    frame._flush_execution_step_persists_sync()

    assert finished.is_set()
    assert persisted == [("chat-active", {"list_text": "step 1"})]
    assert frame._execution_step_persist_thread is None
    assert not frame._execution_step_persist_worker_running


def test_pending_execution_flush_consumes_old_turn_notification_without_reviving_row(frame, monkeypatch):
    frame.active_chat_id = "chat-active"
    frame.current_chat_id = "chat-active"
    frame.active_turn_idx = 2
    frame.view_mode = "active"
    step = {"turn_idx": 1, "display_kind": "commentary", "list_text": "step 1", "detail_text": "step 1"}
    frame._current_chat_state = {"id": "chat-active", "turns": [], "detail_panel_mode": "execution", "execution_steps": [step]}
    frame._apply_detail_panel_mode("execution", refresh_execution=True)
    frame._pending_execution_tail_appends = {"chat-active": [(0, copy.deepcopy(step))]}

    frame._flush_pending_background_ui_updates()

    assert frame._pending_execution_tail_appends == {}
    assert frame.execution_list.GetString(0) == "暂无执行过程"


def test_appending_execution_entry_keeps_latest_100_content_rows_including_answer(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_turn_idx = 1
    frame._current_chat_state = {
        "id": "chat-current",
        "turns": [
            {"question": "old", "answer_md": "old", "model": "codex/main"},
            {"question": "q", "answer_md": "a", "model": "codex/main"},
        ],
        "detail_panel_mode": "execution",
        "execution_steps": [
            {"step": f"process {idx}", "list_text": f"process {idx}", "display_kind": "commentary", "turn_idx": 1}
            for idx in range(100)
        ],
    }
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    frame._render_execution_list()

    frame._append_execution_entry_to_chat(
        "chat-current",
        {"step": "process 100", "list_text": "process 100", "display_kind": "commentary", "turn_idx": 1},
    )

    visible = _execution_content_labels(frame)
    assert visible[0] == "更多"
    assert "process 0" not in visible
    assert visible == ["更多"] + [f"process {idx}" for idx in range(2, 101)] + ["小诸葛：a"]
    assert len(_execution_content_labels(frame)) == 101


def test_switch_current_chat_does_not_load_full_execution_steps(frame, monkeypatch):
    calls = []
    chat = {
        "id": "chat-heavy",
        "title": "heavy",
        "turns": [{"question": "q", "answer_md": "a", "model": "codex/main"}],
        "execution_steps": [{"step": "heavy"}],
    }
    frame.archived_chats = [chat]
    frame._chat_store_enabled = True
    monkeypatch.setattr(frame, "_find_archived_chat", lambda chat_id: chat)

    def fake_hydrate(source_chat, *, include_execution_steps=True):
        calls.append(include_execution_steps)
        return dict(source_chat, execution_steps=[] if not include_execution_steps else source_chat["execution_steps"])

    monkeypatch.setattr(frame, "_hydrate_chat_from_store", fake_hydrate)
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda *args, **kwargs: None)

    assert frame._switch_current_chat("chat-heavy") is True

    assert calls == [False]


def test_idle_execution_flush_does_not_reschedule_when_nothing_is_dirty(frame, monkeypatch):
    frame._execution_list_dirty = False
    delayed = []
    monkeypatch.setattr(frame, "_primary_navigation_control_has_focus", lambda: True)
    monkeypatch.setattr(frame, "_call_later_if_alive", lambda delay_ms, fn, *args: delayed.append((delay_ms, fn, args)) or object())

    frame._flush_idle_ui_refreshes()

    assert delayed == []


def test_deferred_execution_repaint_waits_while_primary_navigation_is_quiet(frame, monkeypatch):
    frame._current_chat_state = {"id": "chat-current", "turns": [], "detail_panel_mode": "execution", "execution_steps": []}
    frame._execution_list_deferred_repaint = True
    frame._execution_list_deferred_select_latest = True
    # Focus alone must not suppress changed content after quiet ends.
    frame._navigation_quiet_until = time.monotonic() + main.NAVIGATION_QUIET_SECONDS
    delayed = []
    monkeypatch.setattr(frame, "_primary_navigation_control_has_focus", lambda: True)
    monkeypatch.setattr(frame, "_request_listbox_repaint", lambda *controls: pytest.fail("focused controls should not repaint execution list immediately"))
    monkeypatch.setattr(frame, "_call_later_if_alive", lambda delay_ms, fn, *args: delayed.append((delay_ms, fn, args)) or object())

    frame._flush_deferred_execution_list_updates()

    assert frame._execution_list_dirty is True
    assert delayed == [(main.IDLE_UI_REFRESH_DELAY_MS, frame._flush_idle_ui_refreshes, ())]


def test_codex_execution_event_appends_after_current_question(frame):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_codex_thread_id = "thread-current"
    frame.active_codex_turn_id = "turn-current"
    frame.active_turn_idx = 0
    frame.active_session_turns = [
        {
            "question": "question",
            "answer_md": main.REQUESTING_TEXT,
            "model": main.DEFAULT_CODEX_MODEL,
            "codex_thread_id": "thread-current",
            "codex_turn_id": "turn-current",
        }
    ]
    frame._current_chat_state = {
        "id": "chat-current",
        "turns": frame.active_session_turns,
        "detail_panel_mode": "execution",
        "execution_steps": [],
    }
    frame._apply_detail_panel_mode("execution", refresh_execution=True)

    assert _execution_content_labels(frame) == ["我：question"]

    frame._on_codex_event_for_chat(
        "chat-current",
        main.CodexEvent(
            type="plan_updated",
            thread_id="thread-current",
            turn_id="turn-current",
            text="checking files",
        ),
    )

    assert _execution_content_labels(frame) == ["我：question", "计划：checking files"]
    assert [meta[0] for meta in frame.execution_meta if meta[0] != "time"] == ["execution", "execution"]


def test_execution_replay_reconciles_stale_tail_without_cross_turn_row(frame):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_turn_idx = 1
    frame.active_session_turns = [
        {"question": "old question", "answer_md": "old answer", "model": main.DEFAULT_MODEL_ID},
        {"question": "current question", "answer_md": main.REQUESTING_TEXT, "model": main.DEFAULT_MODEL_ID},
    ]
    step = {"id": "current-step", "turn_idx": 1, "display_kind": "commentary", "list_text": "current step", "detail_text": "current step"}
    frame._current_chat_state = {
        "id": "chat-current",
        "turns": frame.active_session_turns,
        "detail_panel_mode": "execution",
        "execution_steps": [step],
    }
    frame._render_execution_list()
    frame.execution_list.Append("stale execution tail")
    frame.execution_meta.append(("execution", 99, "stale execution tail", "stale execution tail"))

    assert frame._append_visible_execution_entry(frame._current_chat_state, 0, step) is True

    rows = list(frame.execution_list.GetStrings())
    assert "stale execution tail" not in rows
    assert rows.count("current step") == 1
    assert len(rows) == len(frame.execution_meta)
    assert not frame._append_visible_execution_entry(
        frame._current_chat_state,
        0,
        {"id": "old-step", "turn_idx": 0, "display_kind": "commentary", "list_text": "old step", "detail_text": "old step"},
    )
    assert "old step" not in list(frame.execution_list.GetStrings())


def test_new_authoritative_execution_row_replaces_a_stale_physical_tail(frame):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_turn_idx = 0
    frame.active_session_turns = [
        {"question": "current question", "answer_md": main.REQUESTING_TEXT, "model": main.DEFAULT_MODEL_ID},
    ]
    step = {
        "id": "new-step",
        "turn_idx": 0,
        "created_at": 2.0,
        "display_kind": "commentary",
        "list_text": "new step",
        "detail_text": "new step",
    }
    frame._current_chat_state = {
        "id": "chat-current",
        "turns": frame.active_session_turns,
        "detail_panel_mode": "execution",
        "execution_steps": [step],
    }
    frame.execution_list.Append("stale execution tail")
    frame.execution_meta.append(("execution", 99, "stale execution tail", "stale execution tail"))

    assert frame._append_visible_execution_entry(frame._current_chat_state, 0, step) is True

    rows = list(frame.execution_list.GetStrings())
    assert "stale execution tail" not in rows
    assert rows.count("new step") == 1
    assert len(rows) == len(frame.execution_meta)


def test_execution_store_merge_uses_stable_identity_and_timestamp_order(frame):
    old_persisted = {
        "id": "shared-step",
        "turn_idx": 0,
        "created_at": 2.0,
        "display_kind": "commentary",
        "list_text": "old label",
        "detail_text": "same detail",
    }
    newer_memory = {
        "detail_text": "same detail",
        "list_text": "new label",
        "display_kind": "commentary",
        "created_at": 2.0,
        "turn_idx": 0,
        "id": "shared-step",
    }
    earliest = {
        "id": "early-step",
        "turn_idx": 0,
        "created_at": 1.0,
        "display_kind": "commentary",
        "list_text": "early",
        "detail_text": "early",
    }
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_turn_idx = 0
    frame._current_chat_state = {
        "id": "chat-current",
        "turns": [{"question": "q", "answer_md": main.REQUESTING_TEXT}],
        "execution_steps": [newer_memory],
    }
    frame._chat_store_enabled = True
    frame.chat_store = SimpleNamespace(
        load_recent_execution_steps=lambda *_args, **_kwargs: (3, [old_persisted, earliest]),
    )

    total, rows = frame._current_execution_steps_for_render()

    assert total == 2  # Available merged rows, rather than unread raw status rows.
    assert [row["id"] for row in rows] == ["early-step", "shared-step"]
    assert rows[1]["list_text"] == "new label"


@pytest.mark.parametrize("question,answer,expected", [
    ("", "", ["暂无执行过程"]), ("q", "", ["我：q"]), ("", "a", ["小诸葛：a"]),
])
def test_execution_empty_turn_context_rows(frame, question, answer, expected):
    frame.active_turn_idx = 0
    frame._current_chat_state = {"id": "chat-current", "detail_panel_mode": "execution",
                                 "turns": [{"question": question, "answer_md": answer}], "execution_steps": []}
    frame._render_execution_list()
    assert _execution_content_labels(frame) == expected


def test_execution_turn_context_rows_inherit_recorded_turn_timestamp(frame):
    frame.active_turn_idx = 0
    frame._current_chat_state = {
        "id": "chat-current",
        "turns": [{"question": "q", "answer_md": "a", "created_at": 1234.5}],
    }

    projected = frame._execution_turn_context_steps([])

    assert [row["created_at"] for row in projected] == [1234.5, 1234.5]


def test_execution_turn_context_prefers_existing_projection_times_and_keeps_invalid_unknown(frame):
    frame.active_turn_idx = 0
    frame._current_chat_state = {
        "id": "chat-current",
        "turns": [{"question": "q", "answer_md": "a", "created_at": "invalid"}],
    }
    steps = [
        {"display_kind": "question", "turn_idx": 0, "created_at": 42.0},
        {"display_kind": "final", "turn_idx": 0, "ts": 43.0},
    ]

    projected = frame._execution_turn_context_steps(steps)

    assert projected[0]["created_at"] == 42.0
    assert projected[-1]["created_at"] == 43.0
    frame._current_chat_state["turns"][0]["created_at"] = float("nan")
    unknown = frame._execution_turn_context_steps([])
    assert all("created_at" not in row for row in unknown)


def test_execution_same_state_replay_has_zero_control_operations(frame, monkeypatch):
    frame._current_chat_state = {"id": "chat-current", "detail_panel_mode": "execution", "turns": [],
                                 "execution_steps": [{"step": "same"}, {"step": "same"}]}
    frame._render_execution_list()
    assert len(set(_execution_content_ids(frame))) == 2
    for method in ("Clear", "Append", "Insert", "Delete", "SetString", "SetSelection"):
        monkeypatch.setattr(frame.execution_list, method, lambda *args: pytest.fail("unchanged execution wrote control"))
    monkeypatch.setattr(frame, "_request_listbox_repaint", lambda *args: pytest.fail("unchanged execution repainted"))
    frame._append_visible_execution_entry(frame._current_chat_state, 1, frame._current_chat_state["execution_steps"][1])


def test_execution_quiet_sync_failure_automatically_retries_notification(frame, monkeypatch, wx_app):
    monkeypatch.setattr(main, "_wx_app_allows_ui_timers", lambda: True)
    event_loop = wx.GUIEventLoop()
    activator = wx.EventLoopActivator(event_loop)
    frame.active_chat_id = frame.current_chat_id = "chat-current"
    frame._current_chat_state = {"id": "chat-current", "detail_panel_mode": "execution", "turns": [],
                                 "execution_steps": [{"step": "canonical"}]}
    frame._render_execution_list()
    frame._pending_execution_tail_appends = {"chat-current": [(0, {"step": "stale"})], "other-chat": [(0, {})]}
    original = frame.execution_list_model.replace_visible_page
    attempts = []
    def flaky(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("temporary failure")
        return original(*args, **kwargs)
    monkeypatch.setattr(frame.execution_list_model, "replace_visible_page", flaky)
    monkeypatch.setattr(frame, "_primary_navigation_control_is_recently_active", lambda: False)
    frame._flush_pending_execution_tail_appends()
    assert "chat-current" in frame._pending_execution_tail_appends
    assert frame._execution_list_dirty
    deadline = time.monotonic() + 2
    while frame._execution_list_dirty and time.monotonic() < deadline:
        wx_app.Yield()
    assert len(attempts) == 2, (frame._idle_ui_refresh_scheduled, frame._idle_ui_refresh_timer, frame._execution_list_dirty, frame._execution_retry_after, time.monotonic())
    assert _execution_content_labels(frame) == ["canonical"]
    assert len(attempts) == 2
    assert set(frame._pending_execution_tail_appends) == {"other-chat"}


def test_execution_history_memory_overrides_hidden_rows_before_more_decision(frame, tmp_path, wx_app):
    store = main.ChatStore(tmp_path / "overrides.db", max_execution_steps_per_turn=1000)
    store.initialize()
    store.upsert_chat({"id": "history", "detail_panel_mode": "execution"})
    store.replace_turns("history", [{"question": "old q", "answer_md": "old a"},
                                    {"question": "new q", "answer_md": "new a"}])
    for i in range(230):
        store.append_execution_step("history", {"id": str(i), "turn_idx": int(i >= 100),
                                                "created_at": i + 1, "step": f"step {i}"})
    memory = [dict(row, display_kind="command") for row in store.load_recent_execution_steps("history", limit=50)[1]]
    frame.chat_store = store
    frame._chat_store_enabled = True
    frame.archived_chats = [{"id": "history", "turns": store.load_chat("history")["turns"],
                             "detail_panel_mode": "execution", "execution_steps": memory}]
    frame.view_mode, frame.view_history_id = "history", "history"
    frame._render_execution_list(force=True)
    deadline = time.monotonic() + 2
    while getattr(frame, "_execution_scan_pending", None) and time.monotonic() < deadline:
        wx_app.Yield()
    frame._render_execution_list(force=True)
    labels = _execution_content_labels(frame)
    assert labels == ["更多"] + [f"step {i}" for i in range(81, 180)] + ["小诸葛：new a"]
    assert "小诸葛：old a" not in labels
    frame.execution_list.SetSelection(20)
    selected = frame.execution_list_model.selected_id()
    # Loading the same records fully must preserve both row IDs and selection.
    full = store.load_execution_steps("history")
    full[-50:] = memory
    frame.archived_chats[0]["execution_steps"] = full
    frame._render_execution_list(force=True)
    assert _execution_content_labels(frame) == labels
    assert frame.execution_list_model.selected_id() == selected
    assert frame.execution_list.GetSelection() == 20


def test_execution_new_row_identity_survives_persistence(frame, tmp_path):
    store = main.ChatStore(tmp_path / "identity.db")
    store.initialize()
    frame.current_chat_id = frame.active_chat_id = "identity"
    frame._current_chat_state = {"id": "identity", "turns": [], "detail_panel_mode": "execution", "execution_steps": []}
    frame._append_execution_entry_to_chat("identity", {"item_id": "provider", "step": "new"}, save_state=False)
    selected = frame.execution_list_model.selected_id()
    store.upsert_chat({"id": "identity"})
    store.append_execution_step("identity", frame._current_chat_state["execution_steps"][0])
    frame._current_chat_state["execution_steps"] = store.load_execution_steps("identity")
    frame._render_execution_list(force=True)
    assert frame.execution_list_model.selected_id() == selected
    assert frame.execution_list_model.selected_id() == selected


@pytest.mark.parametrize("provider", ["codex", "kimi"])
def test_execution_drain_projection_failure_retries_and_keeps_queue_and_persistence(frame, monkeypatch, wx_app, provider):
    event_loop = wx.GUIEventLoop()
    activator = wx.EventLoopActivator(event_loop)
    monkeypatch.setattr(main, "_wx_app_allows_ui_timers", lambda: True)
    monkeypatch.setattr(frame, "_primary_navigation_control_is_recently_active", lambda: False)
    monkeypatch.setattr(frame, "_navigation_quiet_active", lambda: False)
    frame.current_chat_id = frame.active_chat_id = "drain"
    frame._current_chat_state = {"id": "drain", "turns": [], "detail_panel_mode": "execution", "execution_steps": []}
    monkeypatch.setattr(frame, f"_{provider}_ui_event_batch_size", lambda: 1)
    def event(chat_id, value):
        frame._current_chat_state["execution_steps"].append({"step": value.text})
        frame._request_execution_list_sync(frame._current_chat_state)
    monkeypatch.setattr(frame, f"_on_{provider}_event_for_chat", event)
    persisted = []
    monkeypatch.setattr(frame, "_start_execution_step_persist_worker", lambda: persisted.append(1))
    projection = frame._execution_page_projection
    attempts = []
    def flaky():
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("one read failed")
        return projection()
    monkeypatch.setattr(frame, "_execution_page_projection", flaky)
    setattr(frame, f"_pending_{provider}_ui_events", [
        ("drain", main.CodexEvent(type="plan_updated", text=str(i))) for i in range(3)])
    getattr(frame, f"_drain_{provider}_ui_events")()
    assert persisted == [1]
    assert frame._execution_list_dirty
    deadline = time.monotonic() + 3
    while (frame._execution_list_dirty or getattr(frame, f"_pending_{provider}_ui_events")) and time.monotonic() < deadline:
        wx_app.Yield()
    assert list(frame.execution_list.GetStrings()) == [
        main.UNKNOWN_TIME_LABEL, "0",
        main.UNKNOWN_TIME_LABEL, "1",
        main.UNKNOWN_TIME_LABEL, "2",
    ]
    assert len(persisted) == 3
    assert not getattr(frame, f"_pending_{provider}_ui_events")
    assert not frame._execution_list_dirty


def test_execution_kimi_changed_batch_projects_and_repaints_once(frame, monkeypatch):
    frame.current_chat_id = frame.active_chat_id = "kimi-batch"
    frame.active_turn_idx = 0
    frame.active_session_turns = [{"question": "q", "answer_md": main.REQUESTING_TEXT, "model": "kimi/kimi-for-coding"}]
    frame._current_chat_state = {"id": "kimi-batch", "turns": frame.active_session_turns,
                                 "detail_panel_mode": "execution", "execution_steps": []}
    monkeypatch.setattr(frame, "_save_state", lambda *a, **kw: None)
    monkeypatch.setattr(frame, "_defer_codex_state_save", lambda: None)
    monkeypatch.setattr(frame, "_navigation_quiet_active", lambda: False)
    frame._render_execution_list(force=True)
    syncs, repaints = [], []
    original = frame.execution_list_model.replace_visible_page
    def sync(*args, **kwargs):
        syncs.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(frame.execution_list_model, "replace_visible_page", sync)
    monkeypatch.setattr(frame, "_request_listbox_repaint", lambda control: repaints.append(control))
    frame._pending_kimi_ui_events = [("kimi-batch", main.CodexEvent(
        type="plan_updated", text=f"plan {i}", data={"turn_idx": 0})) for i in range(4)]
    frame._drain_kimi_ui_events()
    assert len(syncs) == 1
    assert repaints.count(frame.execution_list) == 1
    assert len(frame._current_chat_state["execution_steps"]) == 4
    assert len(_execution_content_labels(frame)) == 5


def test_execution_idle_store_failure_automatically_recovers(frame, monkeypatch, tmp_path, wx_app):
    event_loop = wx.GUIEventLoop()
    activator = wx.EventLoopActivator(event_loop)
    monkeypatch.setattr(main, "_wx_app_allows_ui_timers", lambda: True)
    monkeypatch.setattr(frame, "_primary_navigation_control_is_recently_active", lambda: False)
    store = main.ChatStore(tmp_path / "retry.db")
    store.initialize()
    store.upsert_chat({"id": "history", "detail_panel_mode": "execution"})
    store.append_execution_step("history", {"step": "recovered"})
    frame.chat_store, frame._chat_store_enabled = store, True
    frame.archived_chats = [{"id": "history"}]
    frame.view_mode, frame.view_history_id = "history", "history"
    frame._current_execution_steps()  # Hydrate only metadata before the timer.
    original, attempts = store.load_recent_execution_steps, []
    def flaky(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise OSError("database transiently unavailable")
        return original(*args, **kwargs)
    monkeypatch.setattr(store, "load_recent_execution_steps", flaky)
    frame._mark_execution_list_dirty()
    frame._flush_idle_ui_refreshes()
    assert frame._execution_list_dirty and len(attempts) == 1
    deadline = time.monotonic() + 3
    while frame._execution_list_dirty and time.monotonic() < deadline:
        wx_app.Yield()
    assert _execution_content_labels(frame) == ["recovered"]
    assert len(attempts) == 2
    assert not frame._execution_list_dirty


@pytest.mark.parametrize("step", ["same string", {"step": "same dictionary"}])
def test_execution_repeated_object_occurrences_preserve_second_selection(frame, step):
    frame.active_turn_idx = 0
    frame._current_chat_state = {"id": "repeated", "detail_panel_mode": "execution",
        "turns": [{"question": "", "answer_md": ""}], "execution_steps": [step, step]}
    frame._render_execution_list()
    assert len(set(_execution_content_ids(frame))) == 2
    frame.execution_list.SetSelection(
        _execution_row_index(frame, frame._execution_step_text(step), occurrence=1)
    )
    selected = frame.execution_list_model.selected_id()
    frame._render_execution_list()
    assert frame.execution_list_model.selected_id() == selected
    assert frame.execution_list_model.selected_id() == selected
    frame._current_chat_state["turns"][0]["question"] = "q"
    frame._render_execution_list()
    assert frame.execution_list_model.selected_id() == selected


def test_execution_legacy_partial_overlap_matches_occurrences_without_dropping_rows(frame):
    step = {"step": "same", "turn_idx": 0, "created_at": 1}
    persisted = [dict(step, _store_step_index=i) for i in range(3)]
    merged = frame._merge_execution_page(persisted, [dict(step), dict(step), {"step": "unsaved", "created_at": 2}])
    assert len(merged) == 4
    assert [row["_store_step_index"] for row in merged[:3]] == [0, 1, 2]
    assert merged[-1]["step"] == "unsaved"
    provider_rows = [dict(step, item_id="reused", _store_step_index=i) for i in range(3)]
    assert len(frame._merge_execution_page(provider_rows, [dict(step, item_id="reused")] * 2)) == 3
    distinct = [dict(step, item_id="reused", step="first", _store_step_index=0),
                dict(step, item_id="reused", step="second", _store_step_index=1)]
    tail = dict(step, item_id="reused", step="second")
    assert [row["step"] for row in frame._merge_execution_page(distinct, [tail])] == ["first", "second"]


def test_execution_legacy_provider_source_switch_restores_content_not_index(frame):
    frame._current_chat_state = {"id": "legacy", "detail_panel_mode": "execution", "turns": [],
        "execution_steps": [{"item_id": "a", "step": "a"}, {"item_id": "b", "step": "selected b"}]}
    frame._render_execution_list()
    frame.execution_list.SetSelection(_execution_row_index(frame, "selected b"))
    frame._current_chat_state["execution_steps"] = [
        {"item_id": "earlier", "step": "earlier", "_store_step_index": 0},
        {"item_id": "a", "step": "a", "_store_step_index": 1},
        {"item_id": "b", "step": "selected b", "_store_step_index": 2}]
    frame._render_execution_list()
    assert frame.execution_list.GetStringSelection() == "selected b"
    assert frame.execution_list_model.selected_id().endswith(":store:2")


@pytest.mark.parametrize("failure", ["read", "retry_after", "loading_write"])
def test_execution_owner_change_failure_blocks_old_details_and_clipboard(frame, monkeypatch, failure):
    frame.current_chat_id = frame.active_chat_id = "A"
    frame._current_chat_state = {"id": "A", "turns": [], "detail_panel_mode": "execution",
                                 "execution_steps": [{"step": "private A"}]}
    frame._render_execution_list()
    frame.execution_list.SetSelection(0)
    frame.current_chat_id = frame.active_chat_id = "B"
    frame._current_chat_state = {"id": "B", "turns": [], "detail_panel_mode": "execution",
                                 "execution_steps": [{"step": "B"}]}
    monkeypatch.setattr(frame, "_execution_page_projection", lambda: (_ for _ in ()).throw(OSError("read error")))
    if failure == "retry_after":
        frame._execution_retry_after = time.monotonic() + 1
    if failure == "loading_write":
        monkeypatch.setattr(frame.execution_list_model, "replace_visible_page",
                            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("native write error")))
    frame._rebuild_execution_list_from_state()
    assert frame._selected_execution_text_viewer_content() is None
    assert frame._try_open_selected_execution_detail() is False
    assert frame.execution_meta == [("info", -1, "", "")]
    if failure != "loading_write":
        assert list(frame.execution_list.GetStrings()) == ["正在加载执行过程"]
    copied = []
    monkeypatch.setattr(frame, "_set_clipboard_text", lambda text: copied.append(text) or True)
    event = SimpleNamespace(GetKeyCode=lambda: ord("C"), ControlDown=lambda: True, AltDown=lambda: False,
                            ShiftDown=lambda: False, Skip=lambda: None, StopPropagation=lambda: None)
    frame._on_execution_key_down(event)
    assert all("private A" not in text for text in copied)


def test_execution_idle_timer_after_destroy_does_not_access_controls(frame, monkeypatch, wx_app):
    event_loop = wx.GUIEventLoop()
    activator = wx.EventLoopActivator(event_loop)
    invoked = []
    original = frame._flush_idle_ui_refreshes
    def callback():
        invoked.append(1)
        original()
    monkeypatch.setattr(frame, "_refresh_history", lambda *a: pytest.fail("destroyed frame touched history control"))
    monkeypatch.setattr(frame, "_render_execution_list", lambda **kw: pytest.fail("destroyed frame touched execution control"))
    frame._history_list_dirty = frame._execution_list_dirty = True
    timer = wx.CallLater(1, callback)
    destroy = frame.Destroy
    monkeypatch.setattr(frame, "Destroy", lambda: None)
    destroy()
    try:
        deadline = time.monotonic() + 1
        while not invoked and time.monotonic() < deadline:
            wx_app.Yield()
        assert invoked == [1]
    finally:
        timer.Stop()
        del activator


@pytest.mark.parametrize("provider", ["codex", "kimi"])
def test_lifecycle_envelope_ids_share_stable_tool_identity(frame, provider):
    frame.active_chat_id = frame.current_chat_id = "lifecycle-chat"
    frame._current_chat_state = {"id": "lifecycle-chat", "revision": 3, "turns": [], "execution_steps": []}
    common = {"provider": provider, "thread_id": f"{provider}-session", "session_id": f"{provider}-session",
              "turn_id": "turn-0", "item_id": "tool-7", "tool_call_id": "tool-7",
              "agent_id": "main", "revision": 3, "display_kind": "command"}
    assert frame._append_execution_entry_to_chat("lifecycle-chat", {
        **common, "provider_event_id": "envelope-start", "event_type": "item_started",
        "list_text": "running", "detail_text": "start",
    })
    assert frame._append_execution_entry_to_chat("lifecycle-chat", {
        **common, "provider_event_id": "envelope-complete", "event_type": "item_completed",
        "list_text": "done", "detail_text": "complete",
    })
    rows = frame._current_chat_state["execution_steps"]
    assert len(rows) == 1
    assert rows[0]["list_text"] == "done"
    assert rows[0]["detail_text"] == "startcomplete"


def test_non_store_assembler_same_start_containment_and_conflict(frame):
    frame.active_chat_id = frame.current_chat_id = "fallback"
    frame._current_chat_state = {"id": "fallback", "revision": 1, "turns": [], "execution_steps": []}
    frame._chat_store_enabled = False
    def send(fragment_id, text):
        frame._buffer_execution_delta("fallback", main.CodexEvent(
            type="agent_message_delta", thread_id="thread", turn_id="turn", item_id="stream",
            fragment_id=fragment_id, offset=0, text=text))
    send("long", "abcdef")
    send("short", "abc")
    state = next(iter(frame._execution_delta_buffer.values()))
    assert state["parts"] == ["abcdef"]
    send("longer", "abcdefgh")
    assert state["parts"] == ["abcdefgh"]
    send("bad", "abX")
    assert state["parts"] == ["abcdefgh"]
    assert state["conflicts"][-1]["fragment_id"] == "bad"


def test_non_store_assembler_leading_gap_waits_for_origin(frame):
    frame.active_chat_id = frame.current_chat_id = "fallback-gap"
    frame._current_chat_state = {"id":"fallback-gap","revision":1,"turns":[],"execution_steps":[]}
    frame._chat_store_enabled = False
    def send(fragment_id, offset, text):
        frame._buffer_execution_delta("fallback-gap", main.CodexEvent(
            type="agent_message_delta", thread_id="thread", turn_id="turn", item_id="stream",
            fragment_id=fragment_id, offset=offset, text=text))
    send("late", 3, "def")
    state = next(iter(frame._execution_delta_buffer.values()))
    assert state["pending_gap"] and state["parts"] == []
    assert frame._flush_execution_delta("fallback-gap", "turn") is False
    assert frame._execution_delta_buffer
    send("first", 0, "abc")
    assert not state["pending_gap"] and state["parts"] == ["abcdef"]


def test_non_store_private_latch_redacts_and_avoids_overlap_conflict(frame):
    frame.active_chat_id = frame.current_chat_id = "fallback-private"
    frame._current_chat_state = {"id":"fallback-private","revision":1,"turns":[],"execution_steps":[]}
    frame._chat_store_enabled = False
    base = dict(type="agent_message_delta", provider="kimi", thread_id="session", turn_id="turn",
                item_id="thinking", display_kind="thinking")
    frame._buffer_execution_delta("fallback-private", main.CodexEvent(**base, fragment_id="public", offset=0,
        text="PUBLIC", data={"adapter":"kimi_server","source_kind":"thinking.delta"}))
    frame._buffer_execution_delta("fallback-private", main.CodexEvent(**base, fragment_id="private", offset=6,
        text="SECRET", data={"adapter":"kimi_server","source_kind":"thinking.delta","non_disclosable":True}))
    state = next(iter(frame._execution_delta_buffer.values()))
    assert state["private_reasoning"] and "PUBLIC" not in "".join(state["parts"])
    before_conflicts = len(state["conflicts"])
    frame._buffer_execution_delta("fallback-private", main.CodexEvent(**base, fragment_id="later", offset=3,
        text="LIC-CHANGED", data={"adapter":"kimi_server","source_kind":"thinking.delta"}))
    assert state["private_reasoning"] and len(state["conflicts"]) == before_conflicts


def test_remote_execution_projection_history_latest_plain_answer_owner(frame, tmp_path):
    store = main.ChatStore(tmp_path / "history-latest-answer.db")
    store.initialize()
    turns = [
        {"question": "old q", "answer_md": "old a", "created_at": 1.0},
        {"question": "latest q", "answer_md": "latest a", "created_at": 2.0, "answer_at": 3.0},
    ]
    store.upsert_chat({"id": "history-owner", "title": "history", "turns": turns})
    store.replace_turns("history-owner", turns)
    store.replace_execution_steps("history-owner", [
        {"turn_idx": 0, "display_kind": "commentary", "list_text": "old visible"},
        {"turn_idx": 1, "display_kind": "command", "list_text": "hidden command"},
        {"turn_idx": 1, "event_type": "item_completed", "phase": "final_answer",
         "detail_text": "hidden final"},
    ])
    frame.chat_store = store
    frame._chat_store_enabled = True
    frame.active_chat_id = frame.current_chat_id = "active-owner"
    frame.active_turn_idx = 0
    frame.active_session_turns = [{"question": "active q", "answer_md": "active a"}]
    frame._current_chat_state = {"id": "active-owner", "turns": frame.active_session_turns,
                                 "execution_steps": []}
    frame.archived_chats = [{"id": "history-owner", "turns": turns,
                             "detail_panel_mode": "execution",
                             "execution_steps": store.load_execution_steps("history-owner")}]
    frame.view_mode, frame.view_history_id = "history", "history-owner"

    status, page = frame._remote_api_execution_page_ui(
        {"chat_id": "history-owner", "body": {"limit": 2}}, secret="secret"
    )
    assert status == 200, page
    assert [row["list_text"] for row in page["rows"]] == ["old visible", "小诸葛：latest a"]
    assert page["rows"][-1]["created_at"] == 3.0
    assert page["rows"][-1]["row_id"] == "execution:history-owner:turn:1:answer"
    assert all(row["row_id"].startswith("execution:history-owner:") for row in page["rows"])
    frame._render_execution_list(force=True)
    assert _execution_content_labels(frame) == ["我：latest q", "old visible", "小诸葛：latest a"]
