# Codex CLI 聊天接入设计方案

## 摘要
- 在现有桌面聊天程序中新增 `codex/main` 本地后端，底层不走终端模拟，也不走会话文件轮询作为主链路，统一接 `codex app-server` 的 JSON-RPC 协议。
- 普通 Codex 回复继续进入现有回答列表；结构化 `requestUserInput`、命令审批、权限审批统一弹轻量对话框处理。
- 普通“文本型反问”不弹窗，仍然回主输入框；当 Codex 当前 turn 仍处于活动状态时，用户下一次发送走 `turn/steer`，否则走新的 `turn/start`。
- 需要按协议修正一处实现：`item/tool/requestUserInput` 不能用 `turn/steer` 回答，必须回它对应的 JSON-RPC request response；`turn/steer` 只用于普通文本追问。

## 实现变更
- 新增 `CodexAppServerClient`，负责：
  - 启动和维持单个 `codex app-server --listen stdio://` 子进程。
  - 完成 `initialize` 握手，发送 `thread/start`、`thread/resume`、`turn/start`、`turn/steer`。
  - 监听 `thread/started`、`turn/started`、`turn/completed`、`turn/plan/updated`、`turn/diff/updated`、错误通知，以及服务端 request。
  - 将协议事件转成 GUI 可消费的统一事件对象。
- 在现有聊天状态里新增 Codex 会话字段：
  - `active_codex_thread_id`
  - `active_codex_turn_id`
  - `active_codex_turn_active`
  - `active_codex_pending_prompt`
  - `active_codex_pending_request`
  - `active_codex_request_queue`
- 发送逻辑改成两段式：
  - 当前没有活动 Codex turn 时，主输入框发送触发 `turn/start`。
  - 当前存在活动 Codex turn，且已识别为等待用户补充时，主输入框发送触发 `turn/steer(expectedTurnId=active_codex_turn_id)`。
- UI 行为改成三类：
  - 普通 assistant 最终答复：写入回答列表，沿用当前历史归档和详情页逻辑。
  - `item/tool/requestUserInput`：弹轻量表单对话框。
  - `item/commandExecution/requestApproval`、`item/permissions/requestApproval`：弹审批对话框，展示关键信息并允许 `accept`、`acceptForSession`、`decline`、`cancel` 等映射后的操作。
- 轻量对话框的具体规则固定为：
  - 有 `options` 的 `requestUserInput` 用单选列表呈现。
  - `isOther=true` 时额外展示“其他”输入框。
  - 无 `options` 的 `requestUserInput` 用文本输入框。
  - `isSecret=true` 用密码框。
  - 单次 request 中有多道题时，一个对话框内顺序渲染，提交时按 `question.id -> answers[]` 回传。
- 普通文本型反问的判定规则固定为：
  - 只在当前 turn 仍未 `turn/completed` 时尝试判定。
  - 优先处理结构化 request；只有在没有结构化 request 时，才对最新 assistant 文本做追问识别。
  - 识别命中条件为文本明显是追问或确认语句，且末尾包含 `?` 或 `？`，或命中“请提供 / 请确认 / 告诉我 / 请选择 / 是否”等提示短语。
  - 命中后不弹窗，只设置 `active_codex_pending_prompt`，状态栏提示“Codex 等待你的补充信息”，并将主输入框后续一次发送路由到 `turn/steer`。
- 事件与渲染规则固定为：
  - `commentary` 仅更新状态栏，不入回答列表。
  - `final_answer` 才写入回答列表。
  - `turn/completed` 到达后清理 `active_codex_turn_active`；若此时仍有未处理结构化 request，则以 request 生命周期为准，不允许新建 turn。
  - 多个 server request 并发到达时，进入 FIFO 队列，一次只显示一个弹窗。
- 历史与恢复规则固定为：
  - 当前活跃聊天保存 `thread_id`，归档聊天一并保存 `thread_id`。
  - 载入历史聊天时，如果是 Codex 会话，优先 `thread/resume` 恢复；恢复失败则只展示本地历史，不自动发起新 turn。
  - `新聊天` 对 Codex 模型创建新的 thread，不复用旧 thread。

## 测试计划
- 单元测试：
  - `CodexAppServerClient` 能正确发送 `thread/start`、`turn/start`、`turn/steer`。
  - `requestUserInput`、命令审批、权限审批 request 能正确映射成 UI 事件和协议响应。
  - 普通文本追问在活动 turn 中命中后会把下一次发送切到 `turn/steer`。
  - `turn/completed` 后发送恢复为新的 `turn/start`。
- 集成测试：
  - 选择 `codex/main` 后首轮提问能创建 thread 并显示最终回答。
  - `requestUserInput` 触发时弹窗显示选项，提交后请求被正确响应，聊天继续。
  - 命令审批和权限审批分别弹窗，接受/拒绝能影响后续状态。
  - 普通文本追问不弹窗，只在状态栏提示，用户在主输入框补充后走 `turn/steer`。
  - 历史会话保存和恢复后，能继续使用同一 Codex thread。
- 回归测试：
  - 现有 OpenClaw 路径不受影响。
  - 非 Codex 模型仍走原 `ChatClient` 路径。
  - 回答列表、历史归档、详情页、提示音逻辑保持现状。

## 默认决策与约束
- v1 范围包含三类交互：普通 Codex 回复、`requestUserInput`、审批请求。
- 普通文本追问不弹窗，仍使用主输入框。
- `requestUserInput` 使用协议 request-response 回答，不使用 `turn/steer`。
- `turn/steer` 仅用于 Codex 在活动 turn 中等待用户补充普通文本时的继续输入。
- 一次只允许一个活动 Codex turn；额外请求进入队列，不并行开多个弹窗。
- 主链路只依赖 `app-server`；`~/.codex/sessions/*.jsonl` 仅作为排障日志，不作为运行时同步源。
