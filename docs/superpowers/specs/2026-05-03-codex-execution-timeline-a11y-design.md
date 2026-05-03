# Codex Execution Timeline A11y Design

日期：2026-05-03

## 背景

当前聊天界面按 `F1` 切换出的执行过程列表，只展示“开始执行”“完成执行”“计划更新”这类摘要文案，信息密度明显低于 CLI 窗口中的过程提示。与此同时，现有执行过程列表的刷新方式偏全量重建，不适合读屏用户在列表中稳定浏览。

回答列表已经具备一套可用的无障碍模式：列表中显示适合浏览的文本，同时在内部保存完整正文，后续复制、激活、详情页与读屏相关行为都围绕完整正文展开。执行过程列表需要对齐这套模式，而不是另做一套独立的详情区域方案。

## 目标

- 执行过程列表尽量显示 CLI 中间过程原文，包括命令、发现、推理、下一步、错误输出。
- `codex_client.py` 负责协议归一化，`main.py` 只消费标准化事件。
- 执行过程列表沿用回答列表的 `plain/detail` 模式，不新增额外详情区域。
- 列表视觉上保持单行浏览。
- 长执行项可像回答列表中的长回答一样被完整朗读。
- 实时执行过程中，列表只在形成一条新执行过程时尾部追加一项，不做频繁全量刷新。
- 不抢焦点，不改变当前选中项，不打断读屏浏览。

## 非目标

- 不把执行过程列表改造成多行富文本日志面板。
- 不在主界面新增下方详情面板。
- 不在本期实现完整 diff 浏览器。
- 不改变回答列表既有结构与行为。

## 现状

- `codex_client.py` 已能收到 `item_started`、`item_completed`、`plan_updated`、`diff_updated`、`stderr`、`server_request`、`turn_started`、`turn_completed`、`agent_message_delta` 等事件。
- `main.py` 当前会把这些事件压缩成“开始执行”“完成执行”“计划更新”之类的摘要文案，再写入聊天的 `execution_steps`。
- `agent_message_delta` 当前不会进入执行过程列表，导致 CLI 中的发现、推理、下一步等过程提示被丢弃。
- 执行过程列表的当前渲染路径偏向全量重建，不适合读屏用户稳定浏览中间项。

## 设计概览

方案分为三层：

1. 归一化层：`codex_client.py`
2. 存储层：聊天级 `execution_steps`
3. 展示层：`main.py`

总体原则：

- `codex_client.py` 做协议字段抬升与语义归一化。
- `main.py` 不再解析 `event.data` 的零散结构。
- 执行过程列表视觉上显示单行文本，但内部保留完整正文。
- 实时更新使用增量追加而非全量重建。

## 数据模型

### CodexEvent

建议将 `CodexEvent` 扩展为：

```python
@dataclass
class CodexEvent:
    type: str
    thread_id: str = ""
    turn_id: str = ""
    item_id: str = ""

    text: str = ""
    raw_text: str = ""
    title: str = ""
    command: str = ""
    exit_code: int | None = None

    phase: str = ""
    status: str = ""
    subtype: str = ""
    display_kind: str = ""

    flags: list[str] = field(default_factory=list)
    request_id: str | int | None = None
    method: str = ""
    params: dict = field(default_factory=dict)
    data: dict = field(default_factory=dict)
    usage: dict = field(default_factory=dict)
```

字段约定：

- `text`：规范化后的主文本，保留全文
- `raw_text`：协议原文，保留全文
- `title`：标题、标签、动作名
- `command`：完整命令
- `exit_code`：退出码
- `subtype`：`commandExecution`、`agentMessage`、`fileChange` 等
- `display_kind`：`command`、`commentary`、`error`、`plan`、`status`、`artifact`

### execution_steps

每个聊天里的 `execution_steps` 从旧的单字段结构：

```python
{"step": "..."}
```

升级成富对象：

```python
{
  "event_type": "agent_message_delta",
  "display_kind": "commentary",
  "subtype": "agentMessageDelta",
  "list_text": "先检查 main.py 里 F1 面板和 Codex 事件映射逻辑。 下一步扩展 codex_client.py。",
  "detail_text": "先检查 main.py 里 F1 面板和 Codex 事件映射逻辑。\n下一步扩展 codex_client.py。",
  "raw_text": "先检查 main.py 里 F1 面板和 Codex 事件映射逻辑。\n下一步扩展 codex_client.py。",
  "title": "",
  "command": "",
  "exit_code": None,
  "phase": "analysis",
  "status": "agentMessage",
  "thread_id": "...",
  "turn_id": "...",
  "item_id": "...",
  "created_at": 1710000000.0
}
```

字段约定：

- `list_text`：给 `execution_list` 显示的单行文本
- `detail_text`：给复制、朗读、激活详情页使用的完整文本
- `raw_text`：调试和兼容保留
- `detail_text` 永远优先于 `list_text`

### execution_meta

像回答列表的 `answer_meta` 一样，新增：

```python
self.execution_meta = []
```

每项结构与回答列表保持一致风格：

```python
(item_type, step_idx, plain_text, detail_text)
```

`item_type` 只使用稳定小集合：

- `status`
- `command`
- `commentary`
- `plan`
- `error`
- `artifact`
- `info`

## 事件归一化规则

### item/agentMessage/delta

- `display_kind = "commentary"`
- `subtype = "agentMessageDelta"`
- `text = delta`
- `raw_text = delta`

### item/started 与 item/completed

统一通过 `codex_client.py` helper 归一化，`main.py` 不再自己解析 `item.data`。

#### commandExecution

- `display_kind = "command"`
- `subtype = "commandExecution"`
- `title = item.title/name/label`
- `command = item.command/commandLine/cmd`
- `exit_code = item.exitCode`
- `text = command or item.text or title`
- `raw_text = item.text`

#### agentMessage

- `display_kind = "commentary"`
- `subtype = "agentMessage"`
- `text = item.text`
- `raw_text = item.text`

#### fileChange

- `display_kind = "artifact"`
- `subtype = "fileChange"`
- 若协议无自然语言文本，在 client 层生成简短摘要
- `main.py` 不再自己拼 path 摘要

### turn/plan/updated

- `display_kind = "plan"`
- `text = explanation`
- `raw_text = explanation`

### turn/diff/updated

- `display_kind = "artifact"`
- `text = ""`
- `raw_text = diff`

### stderr

- `display_kind = "error"`
- `text = stderr line`
- `raw_text = stderr line`

### turn/started 与 turn/completed

- `display_kind = "status"`
- 允许 `text` 为空，由 UI 层做稳定兜底

### server_request

- `display_kind = "status"`
- 有原文则保留
- 无原文兜底 `等待用户输入`

## 执行项生成规则

### detail_text

优先使用完整正文：

- `event.text`
- `event.raw_text`
- `event.command`
- `event.title`
- 生命周期兜底文案

### list_text

从 `detail_text` 单行化得到：

- 换行替换为空格
- 连续空白压缩
- 可加轻前缀：
  - `[命令] `
  - `[stderr] `
  - `[计划] `
- 超长时仅截断 `list_text`
- `detail_text` 永不截断

### 特殊项

#### diff_updated

- `list_text = "代码变更已更新"`
- `detail_text = "代码变更已更新"`
- `raw_text` 保留完整 diff

#### turn_started

- `detail_text = "开始处理本轮请求"`

#### turn_completed

- `detail_text = "本轮处理结束"`

## UI 更新策略

### 全量重建仅用于

- 切换到执行过程模式
- 切换聊天
- 查看历史聊天
- 启动恢复状态

建议函数：

- `_rebuild_execution_list_from_state()`

行为：

- `Clear()`
- 重建 `execution_meta`
- 重新 `Append()` 全部当前聊天步骤
- 如无 selection，可只首次设置 selection
- 不 `SetFocus()`

### 增量追加仅用于

- 当前聊天形成一条新的 execution entry
- 当前正显示执行过程模式

建议函数：

- `_append_execution_list_item(step)`

行为：

- `Append(step["list_text"])`
- `execution_meta.append(...)`
- 不 `Clear()`
- 不重建全表
- 不改变 selection
- 不 `SetFocus()`
- 不滚动到末尾

### 实时事件硬规则

实时事件路径禁止调用全量重建函数。

## 无障碍与回答列表一致性

执行过程列表直接复用回答列表模式：

- 列表显示 `plain_text`
- 元数据保存 `detail_text`
- `Ctrl+C` 复制 `detail_text`
- `Enter` / 双击走 detail 行为
- 读屏应基于 `detail_text` 获得完整正文

不增加额外详情面板，不做附件式侧显结构。

## 执行项激活行为

为了和回答列表对齐，建议提供：

- `_try_open_selected_execution_detail()`
- `_ensure_execution_detail_page(step, step_idx)`

行为：

- 为当前执行项生成 HTML 详情页
- 展示完整 `detail_text`
- 可附带元信息：
  - 类型
  - 时间
  - phase
  - status
  - command
  - exit_code

## delta 合并策略

只对 `agent_message_delta` 做缓冲合并。

- key: `(chat_id, turn_id, item_id)`
- 连续 commentary delta 在短时间窗口内拼接
- flush 时机：
  - 收到非 delta 事件
  - 切换聊天
  - 切换到 execution 模式前
  - turn completed
  - 超时

flush 后：

- 只生成一条 commentary execution entry
- 只向列表尾部追加一次
- 避免碎片刷屏

可选去重：

- 若 flush 后文本与上一条 commentary 的 `detail_text` 完全一致，则不追加

## 聊天切换与模式切换

### 当前聊天新事件

- 写入 `execution_steps`
- 若当前显示 execution 模式，尾部追加一项

### 后台聊天新事件

- 只写对应聊天 state
- 不刷新当前列表

### 切到 execution 模式

- 先 flush 当前聊天 pending delta
- 再全量重建列表

### 切换聊天

- 先 flush 旧聊天 pending delta
- 加载新聊天
- 若当前模式为 execution，重建新聊天执行列表

## 兼容性

旧数据格式：

```python
{"step": "..."}
```

兼容策略：

- `list_text = step`
- `detail_text = step`
- `item_type = "info"` 或 `status`

不要求迁移历史存档，新数据直接写新格式。

## 代码改动范围

### codex_client.py

- 扩展 `CodexEvent`
- 新增 item 归一化 helper
- 在 `_emit_protocol_event()` 中把协议事件归一化成富语义事件

### main.py

- 引入 `execution_meta`
- 实现 execution entry 构造函数
- 将执行过程列表改为“全量重建”和“增量追加”两条路径
- 改造 `_on_codex_event_for_chat()`，引入 delta buffer 与 flush
- 调整执行过程列表的 `Ctrl+C`、Enter、双击行为，统一走 `detail_text`
- 新增 execution detail page 生成逻辑
- 保留旧 `execution_steps` 兼容读取

### tests/test_main_unit.py

- 补 execution list 增量追加测试
- 补 `execution_meta` 生成与复制 detail 测试
- 补 delta 合并与 flush 测试
- 补切换聊天、切换模式前 flush 的回归测试
- 补执行项 detail 页入口测试

### tests/test_codex_client_unit.py

- 补协议归一化测试
- 校验 `command/title/exit_code/display_kind/subtype/raw_text` 提取正确

## 测试策略

### codex_client.py

- `commandExecution` 字段抬升正确
- `agentMessage` 保留全文
- `agent_message_delta` 保留全文
- `stderr` 保留全文
- `plan_updated` 保留 explanation

### 执行项生成

- `detail_text` 保留原文
- `list_text` 正确单行化
- 仅 `list_text` 截断
- `diff_updated` 用摘要文案

### UI 增量行为

- 新项只走 `Append`
- 不调用 `Clear`
- 不全量重建
- 不改变 selection
- 不 `SetFocus`

### 回答列表同款交互

- `Ctrl+C` 复制 `detail_text`
- 执行项激活走 detail 页面入口
- `execution_meta` 正确生成
- 超长执行项可完整复制/朗读

### delta 缓冲

- 多个 delta 合并成一条
- flush 后只追加一次
- 切模式/切聊天前会 flush

## 风险与控制

### 协议字段不稳定

风险：

- Codex app-server 的 item 字段名可能存在别名。

控制：

- 所有提取都走 helper，统一兼容 `command`、`commandLine`、`cmd`，`title`、`name`、`label` 等别名。
- `main.py` 不依赖 `data` 原始结构。

### 执行列表误刷新整表

风险：

- 旧路径仍调用全量重建函数。

控制：

- 将全量函数语义明确为仅用于重建。
- 测试断言实时新增不触发 `Clear()`。

### 读屏仍只读到单行文本

风险：

- 如果交互逻辑仍直接使用列表可见文本，将退化回单行朗读。

控制：

- 复制、激活、后续无障碍入口统一从 `execution_meta.detail_text` 取值。

### delta 过碎或丢失

风险：

- 不合并会刷屏，合并不当会漏项。

控制：

- 只合并 commentary delta。
- 非 delta 到来前先 flush。
- 切模式、切聊天、turn completed 前统一 flush。

### diff 过长拖垮体验

风险：

- 把 raw diff 直接作为朗读正文会让体验过重。

控制：

- `diff_updated` 默认只展示摘要文案。
- raw diff 仅保留在底层数据，不作为本期主朗读正文。

## 实施顺序

1. 扩展 `CodexEvent`
2. 重写 `codex_client.py` 归一化逻辑
3. 在 `main.py` 引入 `execution_meta`
4. 实现 execution entry 构造函数
5. 实现执行列表全量重建与增量追加分离
6. 调整执行过程交互为 `plain/detail` 模式
7. 实现 execution detail 页面
8. 加入 delta 缓冲与 flush
9. 补测试

## 成功标准

- `F1` 执行过程列表能显示接近 CLI 的中间过程信息。
- 长执行项可完整朗读。
- 无需新增详情区域。
- 实时更新只追加新项，不频繁刷新整表。
- 不打断读屏用户浏览。
- `main.py` 不再自己猜 Codex 原始 item 结构。
