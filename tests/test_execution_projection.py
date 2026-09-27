from execution_projection import (
    collapse_kimi_execution_lifecycle,
    execution_command_list_text,
    execution_list_text_from_detail,
    should_show_execution_step,
    execution_row_id,
    project_execution_rows,
)


def test_kimi_lifecycle_keeps_first_position_and_latest_content():
    first = {"source_kind": "tool.started", "thread_id": "t", "turn_id": "u", "item_id": "x", "list_text": "start"}
    other = {"source_kind": "tool.started", "thread_id": "t", "turn_id": "u", "item_id": "y", "list_text": "other"}
    completed = {"source_kind": "tool.completed", "thread_id": "t", "turn_id": "u", "item_id": "x", "list_text": "done"}

    assert collapse_kimi_execution_lifecycle([first, other, completed]) == [completed, other]


def test_kimi_lifecycle_does_not_merge_across_turns():
    first = {"source_kind": "tool.started", "thread_id": "t", "turn_id": "u1", "item_id": "x"}
    second = {"source_kind": "tool.completed", "thread_id": "t", "turn_id": "u2", "item_id": "x"}

    assert collapse_kimi_execution_lifecycle([first, second]) == [first, second]


def test_execution_content_filter_and_list_text_match_desktop_rules():
    assert not should_show_execution_step({"display_kind": "command", "list_text": "命令：run"})
    assert not should_show_execution_step({"display_kind": "error", "list_text": "错误"})
    assert not should_show_execution_step({"display_kind": "status", "detail_text": "active"})
    assert should_show_execution_step({"display_kind": "commentary", "detail_text": "检查完成"})
    assert should_show_execution_step({"display_kind": "command", "kimi_summary": "已运行工具"})
    assert execution_list_text_from_detail("第一行\n第二行", "plan") == "计划：第一行 第二行"
    assert execution_command_list_text("item_completed", "test", "pytest", 0) == "命令：完成执行 test pytest 退出码：0"


def test_projected_kimi_update_keeps_id_and_duplicate_legacy_text_stays_distinct():
    started = {"source_kind": "tool.started", "thread_id": "thread", "turn_id": "turn",
               "item_id": "item", "display_kind": "commentary", "list_text": "开始"}
    completed = {**started, "source_kind": "tool.completed", "list_text": "完成"}
    def rows(steps):
        return project_execution_rows(
            chat_id="owner", revision=2, steps=steps, turns=[], view_mode="history",
            active_turn_index=-1, selected_model="", requesting_text="...",
            answer_to_plain=lambda answer, model: answer,
        )
    before = rows([started])
    after = rows([completed])
    assert before[0]["row_id"] == after[0]["row_id"]
    assert before[0]["list_text"] == "开始"
    assert after[0]["list_text"] == "完成"
    duplicate = {"display_kind": "commentary", "list_text": "重复"}
    duplicates = rows([duplicate, dict(duplicate)])
    assert len({row["row_id"] for row in duplicates}) == 2
    assert execution_row_id("owner", 2, started) == execution_row_id("owner", 2, completed)
