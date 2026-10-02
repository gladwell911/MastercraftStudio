---
title: 'Codex 命令执行的正式人工审批'
type: 'bugfix'
ticket: ''
created: '2026-10-02'
status: 'built'
route: 'full'
route_source: 'auto'
review: 'none'
review_source: 'pinned'
lenses_ran: []
review_loop_iteration: 0
baseline_revision: '712b3985a476fbf59d571db045654884d6f49c28'
context: ['D:/code/sj/mc/AGENTS.md']
---

<frozen-after-approval reason="用户已授权按明确根因进行最小修复，保护既有修改">

## Intent

MC 固定 Never，Codex 0.159.2 将 PowerShell 的 URL 启动识别为需要审批的操作后直接拒绝。支持用户明确选择 OnRequest，并贯通原生命令审批请求及回复，保留安全判断。

## Boundaries & Constraints

Always：默认保持 Never；新增程序菜单“Codex 执行审批”入口，复用现有选择对话框。用户可选“不询问”和“需要时询问”，选择影响未来线程启动/恢复，不主动改变正在运行的回合。使用现有请求交互及 worker IPC，命令审批显示实际命令、cwd、reason，用户明确批准一次或拒绝；只有原生 availableDecisions 允许时才展示决定。关闭对话框按拒绝处理。回复必须绑定 chat/model/thread/turn/request 和当前 client；跨聊天、清除、旧事件或重复回复不得授权错误请求。

Never：不自动批准、不修改当前 MC 活跃 Never 会话、不重执行已经被拒的动作、不关闭 dangerous 判断、不加 allow 规则；不泛化文件/权限/cloud 审批。不重启或覆盖运行包，不提交全部工作区。

| 场景 | 预期 |
|---|---|
| 默认或非法策略 | 保持 never |
| 用户选择 on-request 后启动/恢复线程 | worker 传递 on-request |
| 当前命令审批，用户批准一次 | 原始 request 收到 accept，准确归属 |
| 拒绝或关闭对话框 | 原始 request 收到 decline，不执行 |
| 旧 owner/client、重复或非 command 请求 | 不产生命令授权 |

</frozen-after-approval>

## Code Map

- `main.py`：程序菜单与 state 保存/恢复；`_handle_codex_request_dialog`、`_on_codex_event_for_chat`、启动 payload。文件已有无关脏修改，仅局部编辑。
- `codex_worker_client.py`：start_turn payload、reply_user_input 可复用 IPC 风格。
- `codex_worker_process.py`：start/resume 当前硬编码 never；`_input_request_clients` 原生请求归属映射；增加专用 command 回复并严格验证。
- `codex_client.py`：已有 `respond_command_approval` 原样序列化 decision；不要改原生策略。
- `tests/test_codex_worker_process.py`、`tests/test_codex_client_unit.py` 及定向 GUI automation：协议、owner guard、菜单和对话框验证。
- 同版本根因见旁边 `diagnose-browser-launch-policy-20261002.md`；不要再次分析其他原因。

## Tasks & Acceptance

- [x] 最小菜单及选择状态；未来启动/恢复传递经过校验的策略。
- [x] command requestApproval 的可访问显示、用户决定、worker 回复和 owner/client guard。
- [x] 运行覆盖上表的定向 unit/worker/model 与相关 GUI automation；真实定时器隔离清理。

Given 默认设置，when 启动或恢复，then 仍为 never。Given 用户选择需要时询问，when 后续启动/恢复，then 为 on-request。Given 当前有效命令审批，when 批准/拒绝/关闭，then 仅正确原生请求获得相应决定。Given owner 已变化，when 旧回复到达，then 不批准。

## Implementation Notes
2026-10-02 director：新增“Codex 执行审批”菜单，复用 `CodexUserInputDialog`，保存/加载 `never` 或 `on-request`，非法值回落 `never`，选择时不调用原生线程或修改活跃回合。worker 新建/恢复线程按校验策略传递，命令审批使用独立 IPC，不复用问答回复。

命令请求仅在临时对话框显示实际 command/cwd/reason，不写执行历史或 pending state；显式 `availableDecisions` 仅呈现 `accept` 单次和 `decline`，缺省/null 按原生默认两者处理，关闭按 decline（原生只支持 cancel 时使用 cancel）。回复核对 chat/model/thread/turn/request、turn index/generation 与原 client，拒绝旧 owner、清除后的代际、非 command、重复请求/回复；菜单和对话框均未自动批准。

本任务自改产品文件：`main.py`、`codex_worker_client.py`、`codex_worker_process.py`、`codex_worker_protocol.py`；测试：`tests/test_codex_worker_process.py` 仅增加 fake command response 方法，新建 `tests/test_codex_command_approval_unit.py` 与 `tests/test_codex_command_approval_ui_automation.py`。其余既有脏改动保持，未打包、重启 MC、运行已拒命令、添加规则或提交。`codex_client.py` 原生 reply 无需修改，通过序列化断言核对。

director 自验：新 approval unit、真实 wx GUI automation 与既有 worker 测试合计 66 passed（3.71s）；覆盖默认/非法策略 start/resume、原生决定序列化、准确 owner/重复/旧 client、菜单保存及恢复、实际 server_request retired/current 入口、批准/拒绝/关闭和对话框期间聊天/清除/client 变化。GUI 串行并从 frame 创建前追踪/清理真实 timer；`git diff --check` 通过。待 engineer 独立复验，仅限本任务定向范围，不能称为全套回归或真实模型审批成功。

既有脏文件：chat_store.py、execution_projection.py、main.py、tests/test_answer_list_time_rows_unit.py、tests/test_chat_store_unit.py、tests/test_codex_integration.py、tests/test_kimi_integration.py，必须保留。用户已有明确授权和实施方案，按 director → engineer 执行，不新增审批流程或独立审查轮次。

## Verification

使用已有 `.venv/Scripts/python.exe -m pytest`，只运行变更涉及的 worker/client/model 和 GUI 用例。GUI 串行运行；不启动真实模型、浏览器或操作运行中的 MC。engineer 独立核对实现和本任务差异后复验。

### Engineer 独立复验（2026-10-02）

只读核对本任务 worker/client/protocol/main 差异及新增定向用例；保留既有七份脏修改，未改产品或测试代码。

- `.venv/Scripts/python.exe -m pytest tests/test_codex_command_approval_unit.py tests/test_codex_worker_process.py -q`：58 passed。
- `.venv/Scripts/python.exe -m pytest tests/test_codex_command_approval_ui_automation.py -q`：8 passed；GUI 串行，fixture 在 frame 构造前跟踪定时器并在销毁前清理。
- `git diff --check`：通过，仅有仓库既有换行提示。

共 66 项通过，无确认失败。证据覆盖 start/resume 默认与非法策略回落、OnRequest、原生 request id/decision 与 IPC 序列化、owner/client 守卫、单次批准/拒绝及重放拒绝、菜单保存恢复与非法值、关闭拒绝、聊天/clear/client 变化、retired/current client 的实际 server_request 入口。未启动真实模型、浏览器或重启已运行的 MC；不代表全模块或真实模型验收。

原实施阶段提交边界：step-05 要求本地提交且禁止自动推送；当时为保护并行修改，未创建 commit 或 push。2026-10-02 用户随后明确调用 neat-freak，授权将已审查的当前项目改动一起提交并正常推送。收尾在合并后的源码上重跑 approval unit/worker/GUI：66 passed（3.73s）。源码已 built，运行包尚未更新。
