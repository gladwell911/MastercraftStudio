---
title: 'MC 和 RC 聊天用量、语音输入、附件与无障碍阅读'
type: 'feature'
ticket: ''
created: '2026-11-22'
status: 'built'
route: 'full'
route_source: 'auto'
review: 'thorough'
review_source: 'auto'
lenses_ran: [blind-hunter, edge-case-hunter, verification-gap, intent-alignment]
review_loop_iteration: 1
followup_review_recommended: true
context:
  - 'D:/code/sj/mc/AGENTS.md'
  - 'D:/code/sj/rc/AGENTS.md'
warnings: [multiple-goals, oversized]
deferred: []
baseline_revision: 'f9674e1ea448373cf0f8126ef68f9bd97091164b'
rc_baseline_revision: 'b8a233ea8d8321144d47f877c4bd1ff704a5408f'
---

<intent-contract>

## Intent

让 MC 的 Codex/KimiCode 用量可查，区分两侧 Ctrl 的语音写入目标，确认提问框极长原生行的行为，为 OpenRouter Claude 发送实际附件，并让 MC 与 RC 的普通阅读替换冗长网址和文件路径，同时可打开完整原文。

## Boundaries & Constraints

- 两仓基线已保留既有工作。源码、定向验证与隔离设备验证适用，日常程序切包、部署和日常数据变更另按用户授权。
- 保持当前聊天、原生会话、账号和代次的归属。用量更新保持现有键盘焦点和选择，不因后台会话更新重新绘制前台。
- 普通阅读中的替换仅用于所列展示入口。存储、跨端同步、正文恢复、原文、原始文件提取使用完整内容。
- 原生手势与设备支持以实机测试为准。外部 app 拒绝注入时给出反馈，保持既有录音终止和取消语义。

## I/O & Edge-Case Matrix

| 场景 | 输入 | 应观察到的行为 |
|---|---|---|
| 用量有效或缺失 | 最近与累计输入分母不同、缓存为零、输入为零、计数缺失 | 按输入 token 算两项真实比例，已知零缓存显示 0.0%，比例未知显示暂不可用 |
| Kimi 恢复 | V2 顶层 usage、多页 transcript、同 turn 重复及旧 session 回包 | 当前上下文用主代理帧，累计只计一次，同步前切换身份不会串用量 |
| 回答列表 | 总量 243000、缺失、后台重复更新 | 第一项为「累计 243k」或「累计暂无」，当前选中和焦点不跳 |
| 语音 | 右 Ctrl 单击、系统焦点移动，左 Ctrl 录两段、MC 在后台，迟到结果 | 右侧写识别完成时的活动插入点，左侧依次追加两行 `；\n`，后台 MC 不激活，旧录音不能串写 |
| 附件 | Claude PDF、图片、纯文本与不支持文件 | 请求收到可解码的实际内容。不支持、缺文件或无法读取时给出具体提示并保留草稿 |
| 内容过滤 | 裸网址、路径文件、带定位括号、独立 abc.exe、普通文字 | 普通阅读依规则显示替代词，独立文件名和其他文字仍在。原文路径保留内容和身份 |

</intent-contract>

## Code Map

- MC 用量入口：`mc/codex_client.py` 的通知及续读回放，`mc/kimi_server_websocket.py` 的 V2 分发，`mc/kimi_server_v2.py` 的 transcript，`mc/mc_frame_chat_information.py` 的读写与 Alt+Y。
- MC 回答列表及存储：`mc/mc_frame_answers.py` 列表构建，`mc/mc_frame_chat_information.py` 增量表头，`mc/mc_frame_history.py` 保存与归档，`mc/chat_store.py` 硬清。
- MC 语音与输入：`mc/speech_input.py`、`mc/mc_frame_keyboard.py`、`mc/mc_frame_voice.py`、`mc/main.py` 提问框。
- MC 附件与阅读：`mc/mc_frame_requests.py`、`mc/chat_client.py`、`mc/mc_frame_files.py`、`mc/mc_frame_presentation.py`、`mc/mc_frame_dialogs.py`。
- RC 入口：`rc/lib/main.dart` 中的 `buildMessage`、`openMessageMenu`、`openAnswerDetail`，以及 `rc/lib/answer_detail_content.dart`、`rc/lib/answer_plain_text.dart`、`rc/lib/accessibility_focus_bridge.dart`。

## Tasks & Acceptance

### MC

- [x] **1. Codex 的 Alt+Y 缓存命中。** 在 `mc/codex_client.py` 及 `mc/mc_frame_codex.py` 复用原生 last/total、JSONL 回放和已验证的归属路径，接纳 `inputTokens/cachedInputTokens` 及相应旧协议字段。在 `mc/mc_frame_chat_information.py` 紧跟「会话累计 token」显示「缓存命中」和「累计缓存命中」，使用同一分母规则并给两种模型共用。按输入计数加权；拒绝负数、布尔和超出输入的缓存数；未知比例不显示假零。持久化用量与身份，清空或换原生会话时一起失效。
- [x] **2. 修复 KimiCode V2 用量并恢复历史。** 在 `mc/kimi_server_websocket.py` 接纳主代理顶层 `usage`，区分 socket 代次和已订阅 session。允许空闲会话通过首次订阅快照获得上下文，不依赖当前有活动回合。Alt+Y 走 `mc/mc_frame_chat_information.py` 的异步身份校验。V2 不调用旧 REST status/snapshot，在 `mc/kimi_server_v2.py` 从主代理 transcript 分页恢复历史、按 turnId 去重，最新已完成 step 提供近期率，终态 turn 提供累计。实时事件增量更新，重连、重置和未覆盖历史交错时，以完整可恢复扫描与当前归属更新合并。读取失败保留正确身份的上次结果及其状态，缺失值单独显示。现有 V1 通路和额度刷新继续使用。
- [x] **3. 两模型的回答列表第一项。** 让 `mc/mc_frame_answers.py` 完整重建与 `mc/mc_frame_chat_information.py` 增量刷新共同使用会话累计 token。第一项始终有稳定行身份，即使无问答或暂未取得原生数值。使用十进制 k，整千显示整数，非整千采用一致简短格式，确切整数仍见 Alt+Y。标头无回答激活和已读副作用。在 `mc/mc_frame_history.py` 保存活跃聊天和首次归档用量，恢复缺字段旧历史并防止 `mc/chat_store.py` 清空后复活；覆盖当前、历史、分页、切页和后台流转。
- [x] **4. 语音路由与前台保护。** 在 `mc/speech_input.py` 将触发侧与录音代次绑定，右 Ctrl 仅单独轻按即可启动，双击左 Ctrl 启动及既有停止、取消、声音流程保留。普通右 Ctrl 组合键不启动，验证键盘钩子与回退轮询不会重复触发。在 `mc/mc_frame_voice.py` 右侧结果写识别完成时的 Windows 活动编辑光标或选区，包含浏览器及 MC 自己的编辑框，不转写 MC 备用框，不用仅将消息加入系统队列成功来认定浏览器已插入。左侧专门追加至 MC 问题框末，必要时先与已有草稿隔开，每段以一个中文分号和一个换行结束。后台左侧不调用 SetFocus、Raise 或任何前台激活，保持原前台窗口与控件。防止迟到或取消代次结果写入。
- [x] **5. 提问框不按宽度折行。** `mc/main.py` 的日常提问框基线已启用原生 RichEdit 不折行与横向滚动。对源程序原生 HWND 的约 9,800 字符及约 28,000 字符单行记录逻辑文本、导航、横向可读、完整复制及发送的尾标记，区分文本输入上限和超长行布局或读屏限制。加入有效的最小控件调整前比对两组结果；若无可证明的最小调整，保留现有控件与真实换行并记录平台限制，避免为制造视觉通过而插入硬换行、截断数据或更换整个编辑器。
- [x] **6. Claude OpenRouter 附件。** 在 `mc/mc_frame_requests.py` 保留已有运输路由，调用 `mc/chat_client.py` 的普通 OpenRouter Claude 请求时携带附件元数据并构造真实内容块。图片按 MIME 用 `image_url`，PDF 用 `file`、文件名及 Base64 data URL，按 OpenRouter 支持的 PDF 解析方式发送。对支持的纯文本文件读取本地正文并注明文件名。拒绝未支持的扩展名、错误编码及已知不支持所选模态的模型，明确指出原因，发送前校验失败时保留已有草稿。网络请求在后台，禁止把仅有本地路径的文本或无正文请求作为成功。沿用同一会话的正文、附件行、错误反馈与重试入口；CLI Codex/Kimi/Claude Code 和其他既有路由保持其规定行为。
- [x] **7a. MC 普通阅读。** 在 `mc/mc_frame_presentation.py` 构建只用于展示的过滤并接入 `mc/mc_frame_answers.py` 的首次、增量及补齐行，以及 `mc/mc_frame_dialogs.py` 的 Enter 详情。Shift+Enter 网页从所选回答完整源建立，不使用普通过滤，原有网页格式与已读行为保留。完整正文继续供继续提问、提取文件和远程端。与 RC 共用测试用例语料及预期值。

### RC

- [x] **7b. RC 摘要、详情与原文。** 在 `rc/lib/answer_plain_text.dart` 提供局部过滤，`rc/lib/main.dart` 助手回答卡的可见内容及外层读屏标签使用同一结果，保留状态和消息标识。普通双击详情在 Markdown 构建前过滤，同时处理 `rc/lib/answer_detail_content.dart` 的链接语义，防止 Markdown 渲染后重新追加隐藏 href。TalkBack 通过 `Semantics.onTap` 激活；通过 `Semantics.onLongPress` 或等效可访问动作打开原物理长按菜单。把「显示回答原文」置于菜单第一项，待当前 sheet 可访问后，通过 `rc/lib/accessibility_focus_bridge.dart` 按独立的来源/聊天/消息标识请求首项真实焦点，确认页面切换后旧请求不抢焦点。进入标题为「回答原文详情」的页面，复用常规详情的正确正文归属、长正文补拉与失败重试，显示完整内容。原文复制保留 Markdown；文件提取使用完整正文。问题菜单和其他行为仍有效。

### Acceptance Criteria

- Given MC 打开 Codex 或 KimiCode 会话，when 按 Alt+Y，then 按顺序看到上下文、会话累计 token、缓存命中、累计缓存命中，已知百分比可用同一次来源的输入计数复算。
- Given KimiCode 2.5.3 已有多页历史且无任务在运行，when 重新进入该会话后按 Alt+Y，then 当前上下文与主代理快照一致，累计和最近缓存比例能与 transcript 样本对账。切至相同 turnId 的另一会话也无旧结果覆盖。
- Given 两种模型各有一个聊天，when 进入回答列表、接收用量并重开历史，then 第一行满足「累计 243k」或「累计暂无」，焦点和选中项仅按用户导航改变。
- Given 实际打开记事本、浏览器及 MC，when 在非行尾进行右侧语音并在识别完成前换焦点，then 文本只进入完成时的活动位置。when 连续两次从后台对 MC 用左侧录入，then 任何外部应用不失前台，MC 的问题草稿可见两行且每行以中文分号结束。
- Given MC 提问框窄、含正常长行及原生极长行，when 水平滚动、左右或 Home/End、复制以及提交，then 支持长度不因宽度折行，真实换行保持；以实际发送的字符和末尾标记记录极长边界。
- Given 用户在 MC 选择 OpenRouter Claude 并附加 PDF、图片和纯文本，when 发送，then 实际请求包含这些内容且支持的模型能引用合成样本的文件内标记。附件不可用时可在 MC 获取错误并修复草稿。
- Given 同一回答含三类需替代内容，when 在 MC 列表、Enter 详情或 RC 列表、普通详情读取，then 可见文字与读屏分别只读普通文本、替代词和独立文件名。when MC 用 Shift+Enter 或 RC 用长按首项打开原文，then 可读完整内容和末尾标记。RC 的菜单在 TalkBack 开启时先聚焦「显示回答原文」，模拟控件焦点与实际无障碍焦点分别记录。

## Implementation Notes

## Plan Change Log

## Review Triage Log

## Design Notes

- Kimi 2.5.3 的 `session.usage` REST 结构预置零，V2 上下文的来源是订阅主代理后收到的 `{type:"usage",session_id,agent_id,context_tokens,max_context_tokens}`。每个终态 turn 的输入为 `inputTokens + cachedTokens`，因为前者仅含非缓存读取及缓存写入。step 为 `inputOther + inputCacheRead + inputCacheCreation`。按终态 turn 聚合，不再另加 step 或本地 wire 的重复 usage。可先离线解析，受控实际运行时验证后再声明 Kimi V2 端到端成功。
- 共享例子包括 `请访问 https://example.test/a?q=1。` → `请访问 （网址）。`，`D:\\work\\app.py` 或 `lib/main.dart` → `（文件）`，`修复（D:\\work\\app.py:42）后` → `修复（文件）后`，`abc.exe` 保留。覆盖 ASCII/中文括号、Windows/POSIX/相对路径、Markdown 链接与带行号括号，保留后续标点、正常链接标题、文件名和无关普通段落。两端通过相同输入和期待文案检验，正文不回写替换结果。
- OpenRouter 请求依据见 [PDF 输入](https://openrouter.ai/docs/guides/overview/multimodal/pdfs) 和 [图片输入](https://openrouter.ai/docs/guides/overview/multimodal/images)。本地路径由客户端读取；只有正确的目标路由发送明确选择的附件内容。

## Verification

- MC 协议与用量（在 `D:/code/sj/mc`）`.venv/Scripts/python.exe -m pytest -q tests/test_codex_client_unit.py tests/test_kimi_server_client_unit.py tests/test_main_chat_information.py tests/test_chat_information_ui_automation.py`。覆盖真实通知形态、缺失/零字段、完整分页、归属与更新不抢焦点。
- MC 语音与附件（串行 wx）`.venv/Scripts/python.exe -m pytest -q tests/test_voice_input_integration.py tests/test_voice_input_ui_automation.py tests/test_chat_client_unit.py tests/test_chat_attachments_ui_automation.py`。实际 Windows 隔离窗口依 `tests/owned_window_qa.py` 检查前台 HWND、目标光标和真实文字；静态回调或 mock 状态不等同于系统注入通过。OpenRouter 可用时另以隔离小 PDF、图片、文本和合成标记做有界真实验收。
- MC 阅读（串行 wx）`.venv/Scripts/python.exe -m pytest -q tests/test_main_answers.py tests/test_answer_presentation_ui_automation.py tests/test_answer_detail_projection_regression.py tests/test_file_extraction_ui_unit.py`。提问框的 9,800/28,000 字符验收使用可关闭的隔离窗口，证明尾标记的原生读/复制、提交与焦点。
- RC 阅读（在 `D:/code/sj/rc`）`flutter analyze --no-pub lib/main.dart lib/answer_detail_content.dart lib/answer_plain_text.dart`，`flutter test --no-pub test/answer_plain_text_test.dart test/answer_detail_content_test.dart test/answer_file_extraction_test.dart`。另按名字运行新增 RC widget 测试及现有正文恢复、跨来源和列表操作测试，实测 SemanticsAction.tap/longPress 与首次菜单焦点桥目标。不以存在测试但未运行代替通过。
- 可用 Android 环境下跑 `rc/integration_test/answer_recovery_paragraphs_test.dart` 的正文补拉及无障碍树对照，并用有 TalkBack 的设备核对双击、长按、菜单首项语义焦点、普通与原文读序和末尾标记。按实际环境报告验证边界。两仓各自依记录的基线检查差异与既有失败。

## Code Review

### 2026-11-22

两仓同轮审查完成，12 项修复，2 项基线问题延期，2 项意见驳回。审查范围为 MC 与 RC 记录基线以来的 39 个文件。

- [x] [Review][Patch] 高。右 Ctrl 语音只在派发时捕获 HWND，工作线程稍后选择 UIA 元素。同一浏览器 HWND 内的换框或移动光标会改变写入目标。采样并绑定完成时的元素和可用选区，写入前重验。只提供 ValuePattern 的控件不能把并发删除误判为写入成功。 [mc/mc_frame_voice.py:362] [mc/windows_voice_target.py:62]
- [x] [Review][Patch] 高。Kimi 全量扫描可能跨 transcript reset 提交，且增量删除、内嵌 reset 与未恢复的序列缺口会让旧累计继续有效。给重置和缺口加扫描代次，提交前重验，按删除操作失效并重新对账。 [mc/kimi_server_v2.py:161] [mc/kimi_server_v2.py:386]
- [x] [Review][Patch] 高。Kimi V2 主代理快照直接写会话，普通终态处理因无 per-turn pending 数据清掉它，重复的相同快照又被去重。成功结束后保留验证过的同一归属会话用量。 [mc/mc_frame_chat_information.py:1040]
- [x] [Review][Patch] 中。Kimi 异步实时用量只校验 socket 和 session，账号变更或当前聊天用量代次变化后可以给已排队结果补盖新归属。排队前捕获用量归属并在 UI 写回前重验。 [mc/mc_frame_kimi_sessions.py:1242] [mc/mc_frame_chat_information.py:699]
- [x] [Review][Patch] 中。缺 chat.model 的旧聊天忽略实际 turn.model，回落到当前选择的 Codex 后把普通模型的使用量显示成「累计暂无」。复用已有 chat/turn 模型查找，最后才回落到选择值。 [mc/mc_frame_chat_information.py:22]
- [x] [Review][Patch] 高。Claude 附件验证在 wx 提交回调中无界读取所有正文，慢盘、大文件或网络盘导致键盘阻塞。后台做有界读取并校验归属，成功才提交仍相同的草稿。 [mc/mc_frame_requests.py:875] [mc/chat_client.py:43]
- [x] [Review][Patch] 中。OpenRouter 的无状态后续请求仅重放问题和回答文本，附件内容全部丢失，纯附件提问甚至留下无前置用户消息的回答。复用答复所属会话已提交的可读取附件构造对应用户消息，失效附件要有可辨的历史说明。 [mc/chat_client.py:103]
- [x] [Review][Patch] 中。普通阅读把 1/2.5、/quiet 当文件，漏掉括号 URL 的尾段和含空格的文件路径。RC 详情逐 Markdown 文本节点替换会遗漏跨格式片段。收紧真实路径识别并按完整可读文本边界匹配，在两端共享语料验证。 [mc/mc_frame_presentation.py:140] [rc/lib/answer_plain_text.dart:43]
- [x] [Review][Patch] 高。RC 长按菜单打开时捕获按位置生成的 ID，历史前插可让菜单关闭而不打开原文，缺语义别名时还可能为错误回合补拉。菜单打开时捕获原回合的稳定归属，激活使用同一标识。 [rc/lib/main.dart:9659]
- [x] [Review][Patch] 中。RC 首项只尝试一次原生无障碍焦点，失败后的普通焦点语义事件没有执行 Android 的无障碍焦点动作。每次重试前用原 generation、route、owner 验证面板仍可访问。 [rc/lib/main.dart:9649]
- [x] [Review][Patch] 中。Codex 重复同一累计值的成功通知会清掉查询错误，但不触发刷新，已打开对话框持续显示失败。把错误恢复纳入可见变化判定，同时保持真正重复通知静默。 [mc/mc_frame_codex.py:1301]
- [x] [Review][Patch] 中。存量 MC 测试仍断言旧额度行索引、Codex 「暂无」、无累计行的附件行、无分号换行的左侧语音，以及单参数优化回调。更新契约、保留原断言意图。RichEdit 探测要断言采集的视觉行数及横向可读指标，并覆盖真实换行。 [mc/tests/test_chat_information_ui_automation.py:1] [mc/tests/test_main_chat_information.py:65] [mc/tests/test_voice_input_ui_automation.py:18] [mc/tests/test_question_native_long_line.py:42]
- [x] [Review][Defer] RC 历史前插的 Flutter Semantics 断言和文件要约 owner 偏差 [rc/test/widget_test.dart:1] — 延期，独立验收在 `b8a233e` 基线复现。
- [x] [Review][Defer] MC 语音 UI 组合框模型排序与旧聊天快捷键 [mc/tests/test_voice_input_ui_automation.py:166] — 延期，独立验收在 `f9674e1` 基线复现。

#### 驳回

- 低。双 Ctrl 重叠时 hook 使用单一按下标记，基线已有该状态模型。业务要求保留录音过程中任一 Ctrl 结束的交互，单独右 Ctrl 启动有单独门禁。
- 假。缺显式 comtypes.client hiddenimport 不足以说明安装包必然失效。现有 PyInstaller 标准 hook 收集了生成代码的常规依赖，冻结程序的完整 UI Automation 路径尚需隔离验证。

#### 验证边界

- 独立验收验证了真实 OpenRouter Claude 请求回读 PDF、PNG、TXT 三个合成标记，以及隔离 Kimi 2.5.3 空闲订阅 ACK 和空会话用量。
- 完整历史的 Kimi 实时对账、干净冻结包的外部编辑写入、Windows 物理语音手势和实体 TalkBack 首项焦点仍待相应运行环境。RC widget 验证覆盖平台通道请求。


## Auto Run Result

- MC 实现了两模型的会话用量、缓存命中率、累计表头、语音路由、提问框横向阅读、Claude 附件和默认回答过滤。RC 实现了列表及详情过滤、原文菜单、稳定身份与无障碍焦点请求。
- 已修复独立审查的 12 项问题和额外确认的双 Ctrl 重叠误触发。二次验收在旧基线复现了宽套件的既有失败。
- 独立验证中，MC 定向矩阵 438 项通过，其中用量及 Alt+Y 106 项、原生 RichEdit 长行 2 项。分别验证了 9,800 和 28,000 字符的单行、复制和提交。RC 静态分析通过，定向矩阵 51 项通过，其中阅读 34 项、Widget 12 项、来源和文件链路 5 项。
- 实际 OpenRouter Claude Sonnet 请求从合成 PDF、PNG、TXT 中读回三个不同标记。隔离 KimiCode 2.5.3 空会话订阅 ACK 和用量事件通过，真实非空历史的完整分页未验。
- 未打包或切换日常软件。实体 TalkBack、冻结版 Windows 外部编辑写入及物理语音手势仍需实机验收。
