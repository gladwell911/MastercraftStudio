# 常用命令自动化测试方案

## 1. 目标

为“常用命令”功能制定一套以集成测试、端到端测试和 UI 自动化测试为主的回归方案，覆盖：

- 手机端：添加、删除、编辑、向上移动、向下移动
- 电脑端：添加、删除、编辑、向上移动、向下移动

方案优先复用现有自动化基建，避免把主要信心建立在纯单元测试上。对桌面端还要显式覆盖焦点稳定、选择保持和列表刷新最小化，这和项目的读屏兼容要求直接相关。

## 2. 当前实现与现有基建

### 2.1 电脑端

桌面端实现位于 [main.py](/D:/code/sj/mc/main.py:1695) 之后的常用命令 UI 与远程路由逻辑，核心入口包括：

- `_add_common_command`
- `_edit_selected_common_command`
- `_delete_selected_common_command`
- `_move_selected_common_command_up`
- `_move_selected_common_command_down`
- `_remote_api_common_commands_create_ui`
- `_remote_api_common_commands_update_ui`
- `_remote_api_common_commands_delete_ui`
- `_remote_api_common_commands_move_up_ui`
- `_remote_api_common_commands_move_down_ui`

持久化与排序逻辑位于 [common_commands_store.py](/D:/code/sj/mc/common_commands_store.py:1)。

现有自动化基建：

- [test_common_commands_ui_automation.py](/D:/code/sj/mc/tests/test_common_commands_ui_automation.py:1)
- [test_main_unit.py](/D:/code/sj/mc/tests/test_main_unit.py:435)
- [test_remote_nats_unit.py](/D:/code/sj/mc/tests/test_remote_nats_unit.py:115)
- [test_codex_ui_responsiveness_automation.py](/D:/code/sj/mc/tests/test_codex_ui_responsiveness_automation.py:1)

### 2.2 手机端

手机端页面与服务位于：

- [common_commands_page.dart](/D:/code/sj/rc/lib/common_commands_page.dart:10)
- [common_commands_service.dart](/D:/code/sj/rc/lib/common_commands_service.dart:103)

现有自动化基建：

- [common_commands_page_test.dart](/D:/code/sj/rc/test/common_commands_page_test.dart:474)
- [common_commands_service_test.dart](/D:/code/sj/rc/test/common_commands_service_test.dart:712)
- [common_commands_remote_sync_e2e_test.dart](/D:/code/sj/rc/integration_test/common_commands_remote_sync_e2e_test.dart:1)

结论：两端的功能代码和测试骨架都已存在，本次方案应以“补齐分层覆盖和缺口”为主，而不是新建测试体系。

## 3. 测试分层策略

### 3.1 电脑端

电脑端以 `wxPython` 对话框和列表框为真实操作入口，优先顺序应为：

1. UI 自动化测试
2. 远程 API 集成测试
3. 传输路由测试
4. 响应性/无障碍回归测试

原因：

- 用户实际通过列表、按钮、菜单、快捷键操作
- 项目要求不能因为后台刷新而打断键盘导航和读屏
- 仅验证 store 层无法证明焦点、选中项和刷新行为正确

### 3.2 手机端

手机端修改最终都要通过桌面 authority 落盘并回传，因此优先顺序应为：

1. Widget/UI 集成测试
2. 远程服务集成测试
3. 真正跨端 E2E 测试

原因：

- Widget test 适合稳定验证页面行为、弹窗、长按菜单和列表重排
- Service test 适合验证 `observed_revision`、`observed_version`、`device_id`、`request_id`
- 真正的跨端 E2E 才能证明“手机端修改 -> 桌面 authority 生效 -> 手机端重新看到结果”整条链路正确

## 4. 覆盖矩阵

| 端 | 操作 | 首选测试层 | 必验点 |
| --- | --- | --- | --- |
| 电脑端 | 添加 | UI 自动化 | 保存后新行出现，焦点回到列表，新项保持选中 |
| 电脑端 | 编辑 | UI 自动化 | 原行内容更新，选中项 id 不变，列表焦点不丢失 |
| 电脑端 | 删除 | UI 自动化 | 确认后删除，选中移动到相邻项，焦点稳定 |
| 电脑端 | 向上移动 | UI 自动化 + 远程 API | 顺序变化正确，只在当前 section 内移动，焦点留在当前命令 |
| 电脑端 | 向下移动 | UI 自动化 + 远程 API | 顺序变化正确，只在当前 section 内移动，焦点留在当前命令 |
| 手机端 | 添加 | Widget + 跨端 E2E | 页面提交成功，列表刷新，桌面 authority 产生新记录 |
| 手机端 | 编辑 | Widget + 跨端 E2E | 页面更新成功，revision/version 前进，桌面与手机一致 |
| 手机端 | 删除 | Widget + 跨端 E2E | 页面删除成功，手机列表消失，桌面 authority 同步删除 |
| 手机端 | 向上移动 | Widget + 跨端 E2E | 当前 section 内顺序变化，桌面快照一致 |
| 手机端 | 向下移动 | Widget + 跨端 E2E | 当前 section 内顺序变化，桌面快照一致 |

## 5. 现有覆盖与缺口

### 5.1 电脑端现有覆盖

已有覆盖：

- 打开常用命令窗口
- 回车发送
- 菜单键弹出菜单且不扰动选中项
- Delete 删除
- 添加/编辑后恢复焦点和选中
- 刷新时不抢焦点
- 远端 `move_up` 的 section-local 排序

代表性用例：

- [test_common_commands_ui_automation.py](/D:/code/sj/mc/tests/test_common_commands_ui_automation.py:48)
- [test_common_commands_ui_automation.py](/D:/code/sj/mc/tests/test_common_commands_ui_automation.py:153)
- [test_common_commands_ui_automation.py](/D:/code/sj/mc/tests/test_common_commands_ui_automation.py:301)
- [test_main_unit.py](/D:/code/sj/mc/tests/test_main_unit.py:724)

主要缺口：

- 桌面端缺少独立的 `move_down` UI 自动化用例
- `add` 与 `edit` 当前合并在一个测试里，失败定位成本高
- 远端 API 缺少 `move_down`、顶部/底部 no-op 边界用例
- 传输路由测试缺少 `common_commands_move_down`

### 5.2 手机端现有覆盖

已有覆盖：

- 页面初始焦点落在首条命令或“添加”
- 添加成功不重复触发初始焦点
- 长按编辑
- 长按删除
- 长按菜单暴露置顶、上移、下移
- 置顶和移动后列表顺序变化
- 服务层对 `pin` 和 `move_up` payload 字段有断言
- 真机/真远端集成测试目前只覆盖“读取桌面端命令快照”

代表性用例：

- [common_commands_page_test.dart](/D:/code/sj/rc/test/common_commands_page_test.dart:724)
- [common_commands_page_test.dart](/D:/code/sj/rc/test/common_commands_page_test.dart:776)
- [common_commands_page_test.dart](/D:/code/sj/rc/test/common_commands_page_test.dart:818)
- [common_commands_page_test.dart](/D:/code/sj/rc/test/common_commands_page_test.dart:896)
- [common_commands_service_test.dart](/D:/code/sj/rc/test/common_commands_service_test.dart:682)
- [common_commands_remote_sync_e2e_test.dart](/D:/code/sj/rc/integration_test/common_commands_remote_sync_e2e_test.dart:160)

主要缺口：

- Widget 层没有独立的“向下移动”断言用例
- Widget 层的“上移/下移”当前和置顶混在一个测试里
- Service 层没有单独覆盖 `moveCommonCommandDown` 的 payload 断言
- 跨端 E2E 仍是 smoke test，只验证“手机能读到桌面命令”，没有覆盖增删改和上下移动

## 6. 建议新增或重构的用例

### 6.1 电脑端 UI 自动化

文件：`mc/tests/test_common_commands_ui_automation.py`

建议拆分或新增为以下独立用例：

1. `test_ui_automation_add_common_command_creates_row_and_restores_focus`
2. `test_ui_automation_edit_common_command_updates_selected_row_without_focus_loss`
3. `test_ui_automation_delete_common_command_moves_selection_to_adjacent_row`
4. `test_ui_automation_move_common_command_up_updates_order_and_keeps_focus`
5. `test_ui_automation_move_common_command_down_updates_order_and_keeps_focus`

每个用例统一断言：

- 操作前后 `selected_command_id()`
- `common_commands_list.HasFocus()`
- 可见列表顺序
- store snapshot 顺序

其中 `move_down` 需要补一个与现有 `move_up` 对称的场景，至少包含三条未置顶命令，验证中间项下移后顺序变化正确。

### 6.2 电脑端远程 API 集成测试

文件：`mc/tests/test_main_unit.py`

建议新增：

1. `test_remote_common_commands_move_down_returns_section_local_snapshot`
2. `test_remote_common_commands_move_up_noop_at_section_top_returns_unchanged_snapshot`
3. `test_remote_common_commands_move_down_noop_at_section_bottom_returns_unchanged_snapshot`
4. `test_remote_common_commands_move_down_rejects_stale_revision_with_409`
5. `test_remote_common_commands_move_down_rejects_stale_version_with_409`

重点断言：

- 返回体包含 `accepted/revision/commands`
- 不会跨越 pinned/unpinned section 移动
- 边界 no-op 时命令顺序不变，但返回仍可预测

### 6.3 电脑端传输路由测试

文件：`mc/tests/test_remote_nats_unit.py`

建议补充：

1. `test_routes_common_commands_move_down_command`
2. `test_transport_routes_common_commands_move_down_and_publishes_response`

重点断言：

- `type=common_commands_move_down` 正确路由到 handler
- 响应体完整回传

### 6.4 电脑端响应性/无障碍回归

执行现有：

- `mc/tests/test_codex_ui_responsiveness_automation.py`

建议至少保证常用命令相关改动后复跑 `common_commands` 相关用例，重点防止：

- 列表刷新重复 `SetSelection`
- 无变化时抢焦点
- 键盘导航卡顿

## 7. 手机端建议用例

### 7.1 Widget/UI 集成测试

文件：`rc/test/common_commands_page_test.dart`

建议保留现有覆盖，同时拆分或补齐为更明确的动作级用例：

1. `common commands page add command updates list and announces success`
2. `common commands page edit command updates row and announces success`
3. `common commands page delete command removes row and announces success`
4. `common commands page move up reorders within section`
5. `common commands page move down reorders within section`

重点断言：

- 弹窗或底部菜单是否出现
- service 收到正确参数
- 新 snapshot 应用后列表顺序立即变化
- 首次进入时的无障碍焦点请求不会因一次 mutation 再触发

建议把现有“pin and move actions update list order”拆成至少两个测试，把 `move_down` 从“菜单存在”提升到“顺序变化正确”的断言。

### 7.2 手机端服务集成测试

文件：`rc/test/common_commands_service_test.dart`

建议补充：

1. `remote chat service move down payload includes observed fields and device id`
2. `remote chat service maps move down response to updated outcome`

重点断言：

- `type=common_commands_move_down`
- `observed_revision`
- `observed_version`
- `device_id`
- `request_id`

### 7.3 手机端跨端 E2E

文件：`rc/integration_test/common_commands_remote_sync_e2e_test.dart`

当前只验证读取桌面端快照，建议扩展为五个独立场景，或一个完整生命周期场景加四个精简回归场景：

1. `mobile can add common command through desktop authority`
2. `mobile can edit common command through desktop authority`
3. `mobile can delete common command through desktop authority`
4. `mobile can move common command up through desktop authority`
5. `mobile can move common command down through desktop authority`

推荐断言链路：

- 手机端发起 mutation
- 桌面 authority 返回成功
- 手机端再次拉取时看到最新顺序
- 桌面端最终 snapshot 与手机端列表一致

如果要控制成本，可以先实现一个完整生命周期 E2E：

1. 读取桌面种子命令
2. 手机端添加一条命令
3. 手机端编辑该命令
4. 手机端向上移动
5. 手机端向下移动
6. 手机端删除该命令
7. 校验桌面端最终 snapshot

然后在 CI 中保留每个动作单独 case 的较轻量版本，便于定位失败。

## 8. 推荐执行顺序

1. 先补电脑端 `move_down` UI 自动化和远程 API 缺口
2. 再补电脑端传输层 `move_down` 路由
3. 补手机端 widget 层独立的 move up/move down 断言
4. 补手机端 service 层 `move_down` payload 断言
5. 最后扩展手机端跨端 E2E

原因：桌面端是 authority，先把 authority 的 UI、API 和传输契约固定住，手机端 E2E 才不会建立在不稳定基础上。

## 9. 回归命令

### 9.1 电脑端

```powershell
pytest mc/tests/test_common_commands_ui_automation.py -q
pytest mc/tests/test_main_unit.py -k common_commands -q
pytest mc/tests/test_remote_nats_unit.py -k common_commands -q
pytest mc/tests/test_codex_ui_responsiveness_automation.py -k common_commands -q
```

### 9.2 手机端

```powershell
cd rc
flutter test test/common_commands_page_test.dart
flutter test test/common_commands_service_test.dart
flutter test integration_test/common_commands_remote_sync_e2e_test.dart
```

如果该仓库的集成测试仍要求 `flutter drive` 或特定真机环境，则应按现有 mobile E2E harness 的启动方式执行。

## 10. 风险与注意事项

- 桌面端所有列表变更用例都必须断言 `HasFocus()` 和选中项，防止回归到读屏不稳定状态。
- 上移/下移必须验证 section-local 语义，不能只看“顺序变了”。
- 手机端 E2E 必须校验桌面 authority 的最终快照，而不是只看手机端 UI 文本。
- 现有若干测试把多个动作混在同一个 case 里，短期可复用，长期建议拆开，否则失败定位成本高。
- 对 UI 改动，最终回归至少要同时跑桌面 UI 自动化和手机端对应页面测试，符合项目对无障碍与前台稳定性的要求。
