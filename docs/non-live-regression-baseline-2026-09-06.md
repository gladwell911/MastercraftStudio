# 非实时回归失败基线（2026-09-06）

## 记录目的

在 Kimi Code WebSocket 空闲断连修复的发布验证期间，完整非实时回归套件暴露出一批与本次修复不直接相关的既有失败。本文冻结当时的结果，供后续按领域拆分、确认产品契约并逐项修复；它不是“为使测试变绿而回滚现有 UI 语义”的授权。

## 可复现命令与结果

```powershell
.\.venv\Scripts\python.exe -m pytest -m "not live" -q
```

- 执行日期：2026-09-06
- 耗时：411.84 秒
- 结果：`120 failed, 1421 passed, 5 deselected, 10 warnings`
- 代码基线：提交 `9c28e5acf93b26c3b7b6e851a5071a514fb50659`，分支 `fix/kimi-socket-already-closed`。运行时工作区有 16 个未提交条目，包含 Kimi/Codex 修复及其测试；这份基线不等同于干净提交。
- 运行环境：Windows 10 `10.0.26200`、Python `3.11.9`（64 位）、pytest `8.4.2`、wxPython `4.2.5` / wxWidgets `3.2.9`、项目 `.venv` 和交互式 wx GUI 会话。未记录 CPU、内存、显示缩放；性能项不能据此作跨机器阈值比较。
- marker 审计：`.\.venv\Scripts\python.exe -m pytest --collect-only -m "not live" -q` 收集 `1541/1546` 项并排除 5 项。`not live` 只排除标记为 `live` 的测试，未标记的后台线程、CLI 和本地服务测试仍会执行。
- 运行中观察到一次 wx/COM 清理异常：`Windows fatal exception: code 0x8001010d`。当时主线程位于 `tests/conftest.py` 的 frame 清理，另有 `_openclaw_sync_loop` 线程等待；套件主进程随后继续并以测试失败退出。该现象尚未证明因果关系，应作为独立运行时问题复现，而非已确认根因。
- 证据来源：本次终端中的 pytest 完整结果摘要与 `.pytest_cache/v/cache/lastfailed`。未持久化原始 pytest 日志；再次调查时应先重跑上面的命令并将完整输出保存到临时诊断附件。

## 已确认未受本失败基线影响的范围

- Kimi WebSocket 定向单元测试：45/45 通过。
- Kimi 集成与 UI 无障碍定向集：25/25 通过。
- 附件、上下文、文件管理定向集：51 通过。
- Claude 定向集：10 通过（通过 fake CLI，不依赖本机安装 Claude CLI）。
- Codex 客户端、集成和 worker 定向集：124 通过。

## 分组与建议修复顺序

1. **先稳定测试运行时和外部依赖假设**：wx frame 清理、Claude CLI、NATS/cloudflared、OpenClaw。它们会造成跨文件的连锁失败或环境依赖。
2. **确认已交付 UI 语义后更新测试**：时间行、历史仅查看（不切换 active chat）、置顶与排序、执行列表分页和增量刷新。不得为了旧断言删除时间行或恢复旧历史导航语义。
3. **修复桌面/移动端共享交互契约**：Ctrl+方向键、焦点保留、笔记同步冲突副本；这些项目须在真实 wx 焦点路径上分组验证。
4. **最后处理性能回归**：长会话发送超过 0.5 秒的 UI 自动化失败，应先建立可重复的性能测量再改代码。

## 失败清单（120 项）

### 运行时、客户端与性能（24 项）

- `test/test_claudecode_params.py::test_command_building`：`_build_command()` 会调用 `resolve_claudecode_command()`；无本机 Claude CLI 时失败，应显式注入 fake resolver 或 CLI 路径。
- `tests/test_codex_ui_responsiveness_automation.py::test_real_ui_long_session_primary_controls_remain_responsive`：长会话发送耗时超过 0.5 秒。
- `tests/test_mobile_desktop_clear_context_e2e.py::test_mobile_emulator_clear_context_clears_desktop_chat_frame`。
- `tests/test_openclaw_new_chat.py::test_openclaw_new_chat_persists_fresh_empty_session`。
- `tests/test_openclaw_client_unit.py::test_openclaw_client_callback_error_clears_last_context_usage`。
- `tests/test_openclaw_client_unit.py::test_openclaw_client_explains_codex_oauth_refresh_failure`。
- `tests/test_openclaw_client_unit.py::test_openclaw_client_nonzero_result_leaves_last_context_usage_empty`。
- `tests/test_openclaw_client_unit.py::test_openclaw_client_prefers_result_usage_over_nested_tool_usage`。
- `tests/test_openclaw_client_unit.py::test_openclaw_client_stream_chat_ignores_malformed_usage_metadata`。
- `tests/test_openclaw_client_unit.py::test_openclaw_client_stream_chat_parses_event_list_wrapped_events`。
- `tests/test_openclaw_client_unit.py::test_openclaw_client_stream_chat_parses_json_after_plugin_logs`。
- `tests/test_openclaw_client_unit.py::test_openclaw_client_stream_chat_parses_nested_result_payloads`。
- `tests/test_openclaw_client_unit.py::test_openclaw_client_stream_chat_parses_payload_text`。
- `tests/test_openclaw_client_unit.py::test_openclaw_client_stream_chat_parses_wrapped_event_msg`。
- `tests/test_openclaw_client_unit.py::test_openclaw_client_stream_chat_raises_with_payload_error`。
- `tests/test_openclaw_client_unit.py::test_openclaw_client_stream_chat_records_model_usage_metadata`。
- `tests/test_openclaw_client_unit.py::test_openclaw_client_stream_chat_records_usage_from_json_event_stream`。
- `tests/test_openclaw_client_unit.py::test_openclaw_client_stream_chat_uses_model_window_when_usage_lacks_context_window`。
- `tests/test_openclaw_client_unit.py::test_openclaw_client_stream_chat_uses_plain_stdout_when_json_is_not_emitted`。
- `tests/test_openclaw_client_unit.py::test_openclaw_client_treats_plugin_only_output_as_accepted_no_reply`。
- `tests/test_main_remote_nats_unit.py::test_remote_nats_server_starts_fresh_runtime_on_fallback_tcp_port_when_reused_runtime_auth_fails`。
- `tests/test_main_unit.py::test_fixed_domain_nats_runtime_uses_public_runtime_and_status`：固定域名 NATS 与公开运行时状态契约。
- `tests/test_main_unit.py::test_fixed_domain_remote_reenables_autostart_after_stale_disabled_state`：过期禁用状态后的固定域名自动启动契约。
- `tests/test_main_unit.py::test_remote_startup_connectivity_targets_cloudflared_origin_proxy`：远程启动应连接已验证的 cloudflared origin proxy。

### 时间行与上下文使用量语义（6 项）

当前 UI 会在问答前插入 `time` meta 行；旧测试假定上下文行后立即是用户问题，或历史视图第一行不是时间。应更新断言，不应移除时间行。

- `tests/test_context_usage_e2e.py::test_e2e_context_usage_persists_after_restart_and_history_switch`。
- `tests/test_context_usage_ui_automation.py::test_ui_automation_context_usage_row_is_fixed_above_answers`。
- `tests/test_context_usage_ui_automation.py::test_ui_automation_answer_list_arrow_keys_can_select_context_usage_row`。
- `tests/test_context_usage_ui_automation.py::test_ui_automation_native_listbox_arrow_key_reaches_context_usage_row`。
- `tests/test_context_usage_ui_automation.py::test_ui_automation_history_switch_uses_stored_context_usage_then_cli_unknown`。
- `tests/test_context_usage_ui_automation.py::test_ui_automation_context_row_selection_survives_visible_history_usage_refresh`。

### 历史聊天、焦点与快捷键（38 项）

当前语义为历史记录仅查看，不调用 `_switch_current_chat` 改变 active chat；置顶聊天优先，其他聊天按更新时间排序。以下测试需按该语义重建期望，并验证焦点保持。

- `tests/test_voice_input_ui_automation.py::test_ui_automation_model_combo_prioritizes_cli_models`。
- `tests/test_voice_input_e2e.py::test_e2e_ctrl_left_from_input_walks_back_to_newer_chats`。
- `tests/test_voice_input_e2e.py::test_e2e_ctrl_right_from_input_reaches_pinned_and_older_chats`。
- `tests/test_voice_input_e2e.py::test_e2e_ctrl_right_from_input_walks_older_chats`。
- `tests/test_voice_input_e2e.py::test_e2e_ctrl_right_from_startup_history_selection_without_current_chat`。
- `tests/test_voice_input_e2e.py::test_e2e_ctrl_right_accelerator_switches_chat_from_startup_input_focus`。
- `tests/test_voice_input_e2e.py::test_e2e_global_ctrl_arrow_switches_chat_even_when_input_event_path_is_unavailable`。
- `tests/test_voice_input_e2e.py::test_e2e_notes_alt_x_creates_blank_entry_from_detail_list`。
- `tests/test_voice_input_e2e.py::test_e2e_ctrl_right_accelerator_switches_chat_from_any_focused_control[input_edit]`。
- `tests/test_voice_input_e2e.py::test_e2e_ctrl_right_accelerator_switches_chat_from_any_focused_control[history_list]`。
- `tests/test_voice_input_e2e.py::test_e2e_ctrl_right_accelerator_switches_chat_from_any_focused_control[answer_list]`。
- `tests/test_voice_input_e2e.py::test_e2e_ctrl_right_accelerator_switches_chat_from_any_focused_control[send_button]`。
- `tests/test_voice_input_e2e.py::test_e2e_ctrl_right_accelerator_switches_chat_from_any_focused_control[new_chat_button]`。
- `tests/test_voice_input_e2e.py::test_e2e_ctrl_right_accelerator_switches_chat_from_any_focused_control[model_combo]`。
- `tests/test_voice_input_e2e.py::test_e2e_ctrl_right_accelerator_switches_chat_from_any_focused_control[notes_notebook_list]`。
- `tests/test_voice_input_e2e.py::test_e2e_ctrl_right_accelerator_switches_chat_from_any_focused_control[notes_entry_list]`。
- `tests/test_voice_input_e2e.py::test_e2e_ctrl_right_accelerator_switches_chat_from_any_focused_control[notes_editor]`。
- `tests/test_voice_input_e2e.py::test_e2e_ctrl_right_switches_chat_from_any_focused_control[history_list-_on_history_key_down]`。
- `tests/test_voice_input_e2e.py::test_e2e_ctrl_right_switches_chat_from_any_focused_control[answer_list-_on_answer_key_down]`。
- `tests/test_voice_input_e2e.py::test_e2e_ctrl_right_switches_chat_from_any_focused_control[send_button-_on_generic_key_down]`。
- `tests/test_voice_input_e2e.py::test_e2e_ctrl_right_switches_chat_from_any_focused_control[new_chat_button-_on_generic_key_down]`。
- `tests/test_voice_input_e2e.py::test_e2e_ctrl_right_switches_chat_from_any_focused_control[model_combo-_on_generic_key_down]`。
- `tests/test_voice_input_e2e.py::test_e2e_ctrl_right_switches_chat_from_any_focused_control[notes_notebook_list-_on_notes_key_down]`。
- `tests/test_voice_input_e2e.py::test_e2e_ctrl_right_switches_chat_from_any_focused_control[notes_entry_list-_on_notes_key_down]`。
- `tests/test_voice_input_e2e.py::test_e2e_ctrl_right_switches_chat_from_any_focused_control[notes_editor-_on_notes_key_down]`。
- `tests/test_main_unit.py::test_char_hook_ctrl_left_switches_to_previous_chat_from_any_focus`。
- `tests/test_main_unit.py::test_char_hook_ctrl_right_switches_to_next_chat_from_any_focus`。
- `tests/test_main_unit.py::test_ctrl_history_navigation_keeps_focus_on_origin_control`。
- `tests/test_main_unit.py::test_ctrl_history_navigation_keeps_history_order_unchanged`。
- `tests/test_main_unit.py::test_ctrl_history_navigation_stops_at_last_chat_without_wrap`。
- `tests/test_main_unit.py::test_refresh_history_keeps_switched_chat_in_sorted_position`。
- `tests/test_main_unit.py::test_input_key_down_ctrl_left_switches_to_previous_chat`。
- `tests/test_main_unit.py::test_input_key_down_ctrl_right_switches_to_next_chat`。
- `tests/test_main_unit.py::test_generic_key_down_ctrl_right_switches_to_next_chat`。
- `tests/test_main_unit.py::test_answer_key_down_ctrl_right_navigates_history_view`。
- `tests/test_main_unit.py::test_enter_history_view_updates_view_state_and_focus`。
- `tests/test_main_unit.py::test_enter_active_view_clears_history_view_state`。
- `tests/test_main_unit.py::test_google_chat_remains_visible_in_history_after_done`。

### 回答与执行列表增量渲染（24 项）

这些失败涉及“首个真实执行步骤替换占位行”、执行列表当前轮次过滤、最近 100 项分页、流式完成不抢焦点以及回答行增量追加。修复时必须保留屏幕阅读器焦点和批处理重绘合并。

- `tests/test_main_unit.py::test_active_execution_list_shows_only_current_turn_steps`。
- `tests/test_main_unit.py::test_active_turn_completed_in_execution_mode_does_not_rebuild_execution_list`。
- `tests/test_main_unit.py::test_append_execution_entry_preserves_selection_and_appends_at_end`。
- `tests/test_main_unit.py::test_append_execution_step_refreshes_visible_execution_list_without_stealing_focus`。
- `tests/test_main_unit.py::test_appending_execution_entry_keeps_latest_100_rows_with_more_at_top`。
- `tests/test_main_unit.py::test_apply_detail_panel_mode_only_rebuilds_when_entering_execution_mode`。
- `tests/test_main_unit.py::test_context_usage_estimate_applies_state_without_refreshing_visible_header`。
- `tests/test_main_unit.py::test_execution_event_appends_single_row_without_clearing_visible_list`。
- `tests/test_main_unit.py::test_execution_list_defaults_to_latest_100_rows_and_shows_more_at_top`。
- `tests/test_main_unit.py::test_execution_list_more_item_expands_upward_and_hides_when_all_visible`。
- `tests/test_main_unit.py::test_execution_list_uses_recent_store_page_without_full_step_load`。
- `tests/test_main_unit.py::test_final_answer_live_event_in_execution_mode_does_not_rebuild_execution_list`。
- `tests/test_main_unit.py::test_pending_execution_flush_keeps_unappendable_rows`。
- `tests/test_main_unit.py::test_pending_execution_steps_append_after_quiet_in_order`。
- `tests/test_main_unit.py::test_submit_question_appends_answer_rows_without_clearing_existing_answer_list`。
- `tests/test_main_unit.py::test_submit_question_appends_new_question_without_refreshing_answer_list`。
- `tests/test_main_unit.py::test_switch_to_execution_mode_flushes_pending_delta_before_rebuild`。
- `tests/test_main_unit.py::test_execution_list_tab_uses_primary_tab_navigation`。
- `tests/test_main_unit.py::test_submit_question_replaces_empty_answer_state_with_delete_then_append`。
- `tests/test_main_unit.py::test_save_load_preserves_active_chat_detail_panel_state`。
- `tests/test_main_unit.py::test_claudecode_done_does_not_rerender_ui_while_cli_runs`。
- `tests/test_main_unit.py::test_claudecode_submit_sets_resume_recovery_mode`。
- `tests/test_main_unit.py::test_new_chat_clears_active_claudecode_client_before_next_submit`。
- `tests/test_main_unit.py::test_worker_preserves_chat_id_in_done_callback`。

### 聊天生命周期、标题与状态持久化（16 项）

- `tests/test_main_unit.py::test_apply_archived_title_normalizes_legacy_placeholder_fallback`。
- `tests/test_main_unit.py::test_fallback_model_order_for_deepseek_variant`。
- `tests/test_main_unit.py::test_generate_first_question_title_ignores_answer_style_output_for_question`。
- `tests/test_main_unit.py::test_generate_first_question_title_retries_three_times_then_keeps_default`。
- `tests/test_main_unit.py::test_load_project_folder_appends_system_message_and_rerenders`。
- `tests/test_main_unit.py::test_load_state_rebuilds_timestamp_like_archive_titles`。
- `tests/test_main_unit.py::test_next_default_chat_title_treats_legacy_placeholder_as_default`。
- `tests/test_main_unit.py::test_on_close_marks_archived_chat_pending_turns_before_save`。
- `tests/test_main_unit.py::test_on_close_marks_current_chat_pending_turns_before_save`。
- `tests/test_main_unit.py::test_on_close_stops_remote_servers`。
- `tests/test_main_unit.py::test_resolve_chat_target_returns_archived_chat_for_history_id`。
- `tests/test_main_unit.py::test_resolve_chat_target_returns_current_chat_for_blank_id`。
- `tests/test_main_unit.py::test_shared_chat_title_rules_path_points_to_repo_assets`。
- `tests/test_main_unit.py::test_submit_question_marks_turn_pending_with_recovery_metadata`。
- `tests/test_main_unit.py::test_submit_question_renames_placeholder_history_chat_immediately`。
- `tests/test_main_unit.py::test_bind_events_registers_both_hotkey_ids`。

### 笔记同步与冲突副本（12 项）

- `tests/test_notes_desktop_acceptance.py::test_notes_acceptance_detail_view_keeps_history_tab_position`。
- `tests/test_notes_desktop_acceptance.py::test_notes_acceptance_menu_new_entry_creates_blank_draft_and_focuses_editor`。
- `tests/test_notes_desktop_unit.py::test_conflict_copy_labels_use_semantic_source_words`。
- `tests/test_notes_desktop_unit.py::test_import_entries_uses_single_transaction_for_batch`。
- `tests/test_notes_desktop_unit.py::test_notes_remote_ops_refresh_ui_and_hint_current_editing_entry`。
- `tests/test_notes_desktop_unit.py::test_notes_sync_preserves_conflict_copy_on_stale_update`。
- `tests/test_notes_desktop_unit.py::test_remote_conflict_copy_uses_semantic_last_modified_by`。
- `tests/test_notes_desktop_unit.py::test_remote_create_preserves_full_incoming_metadata`。
- `tests/test_notes_desktop_unit.py::test_remote_create_preserves_incoming_entry_id_and_modifier_identity`。
- `tests/test_notes_desktop_unit.py::test_remote_notebook_create_uses_payload_updated_at_fallback`。
- `tests/test_notes_desktop_unit.py::test_remote_notebook_conflict_copy_titles_use_spec_suffix[desktop-8-（冲突副本-电脑）]`。
- `tests/test_notes_desktop_unit.py::test_remote_notebook_conflict_copy_titles_use_spec_suffix[mobile-9-（冲突副本-手机）]`。

## 说明

`.pytest_cache/v/cache/lastfailed` 此时含有 125 个条目，其中以下 5 个来自本次全量运行之前的失败缓存，不能替代上面的 120 项完整运行摘要：

- `tests/test_codex_integration.py::test_codex_final_answer_rerenders_and_focuses_when_final_answer_arrives`
- `tests/test_integration_context.py::test_global_hotkey_switches_visible_chat_content`
- `tests/test_integration_context.py::test_startup_model_prefers_saved_selection_over_startup_default`
- `tests/test_main_unit.py::test_codex_execution_event_replaces_empty_placeholder`
- `tests/test_main_unit.py::test_restart_cloudflared_service_kills_stuck_process_before_restart`

后续每修复一个领域，应先运行其文件级测试，再运行完整非实时套件，并在本文件顶部补充新的日期、命令和结果，不修改本次基线。对于 wx 焦点问题，应在交互式桌面会话中运行对应 `tests/test_voice_input_e2e.py` 或 `tests/test_codex_ui_responsiveness_automation.py` 节点，并同时断言目标列表/输入框仍保持焦点；性能问题应记录机器条件、至少 10 次样本的中位数和最大值后再调整 0.5 秒阈值。
