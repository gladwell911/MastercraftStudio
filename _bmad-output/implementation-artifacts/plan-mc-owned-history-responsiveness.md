---
title: '只显示 MC 创建的聊天并修复剩余界面卡顿'
type: 'feature'
ticket: ''
created: '2026-11-27'
status: 'built'
baseline_revision: '0b4813ce1f2a93f45f729f650d052ae52acd8382'
route: 'full'
route_source: 'auto'
review: 'thorough'
review_source: 'auto'
lenses_ran: ['blind-hunter', 'edge-case-hunter', 'verification-gap', 'intent-alignment']
review_loop_iteration: 0
followup_review_recommended: true
context: ['AGENTS.md', 'docs/remote-control.md', 'docs/voice-input.md', 'docs/runtime-maintenance.md']
warnings: ['multiple-goals', 'oversized']
deferred:
  - summary: >-
      旧 RC 通知和读状态恢复协议仍读取既有权威事实。
    evidence: |-
      MC 历史集合及直接详情均排除旧纯终端记录。已下发和待补同步的通知事实与检查点有独立持久化契约。
    location: 'chat_store_notifications.py:301'
    severity: low
  - summary: >-
      有 MC 关联的设备仍可能在后台枚举供应商会话目录和元数据。
    evidence: |-
      已在读取回合、写库与 wx 通知前过滤纯终端。逐供应商跳过的边际收益和对旧原生身份回放的影响未证实。
    location: 'mc_frame_native_history.py:457'
    severity: low
---

<intent-contract>

## Intent

1. 只在 MC 聊天历史中提供由 MC 创建的聊天，移除「隐藏终端聊天」的设置和菜单。纯终端会话不再自动导入。保留 MC 已有聊天与原生会话关联后的续聊、回放及通知。
2. 用隔离工作负载复评掉队的键盘事件。修复历史缩小后仍有证据的停顿，同时保持读屏控件的焦点及所选项。

## Boundaries & Constraints

- 旧的终端导入行及关联事实留在库里。应用归属用已有的关系判断，导入行 `id == native_identity`，MC 已有关联行的两个 ID 不同，无关联字段的旧 MC 行仍有效。RC 经 MC 创建的聊天属于 MC。旧配置可含已废弃开关或末次打开的终端导入 ID，启动时恢复正常 MC 聊天入口。
- 由 MC 提供的历史集合使用这一归属规则，包括 RC 请求的分页和兼容集合。外部 CLI 本身与 MC 聊天的已有关联仍可继续。保留显式删除、已读归属、权威版本以及关闭时完成已接收写入的约定。仅在隔离数据中验证。

## I/O & Edge-Case Matrix

| 场景 | 预期 |
|---|---|
| 混合旧库，末次打开为纯终端会话 | 启动后菜单无开关，历史、快捷键和 MC 历史集合都只含 MC 行，纯终端数据保留在库中 |
| 已有 MC 聊天与原生会话匹配，终端另开一个纯终端会话 | 已有关联允许同 ID 补齐或续聊，另开的会话不建立 MC 聊天 |
| SQLite 写锁与已读、关窗或清除相遇 | 焦点和按键继续工作，写入只确认原有且有效的回答，关闭前结算已接受写入 |

</intent-contract>

## Code Map

- `main.py`、`mc_frame_navigation.py`、`mc_frame_history.py` 管理菜单、存档加载、导航、同步保存和关闭快照。
- `chat_store.py`、`mc_frame_native_history.py` 提供归属匹配、旧别名、原生同步。控制 UI route、读正文和 merge 前的准入。`mc_frame_remote_routes.py` 另有后台集合分页。
- `mc_frame_history.py` 的 `_persist_chat_history_to_store`、`_chat_turn_dirty_from`、`_capture_close_write_batch` 管理当前所有者、变更回合和退出结算。使用已存在的回合追踪与元数据写入 API。
- `mc_frame_navigation.py` 的 `_jump_to_unread_chat` 可复用 `list_unread_chat_ids`。`mc_frame_history.py` 的 `_confirm_answer_read` 进入 `mark_chat_read` 和出站队列。
- `mc_frame_answers.py` 的 `_render_answer_list`、`_turn_answer_markdown` 是可见正文重复转换的入口，并保留原生行身份。`mc_frame_files.py` 的 `_publish_file_offer_for_record` 和 `_add_file_to_manager_from_dialog` 调用握手和复制，参照已有 worker 路径。

## Tasks & Acceptance

1. [x] `main.py`、`mc_frame_history.py`、`mc_frame_navigation.py`、`chat_store.py`、`mc_frame_native_history.py`、`mc_frame_remote_routes.py` 限制进入 MC 历史的所有者并删除开关。在读取原生正文、写库和投递界面回调之前排除纯终端会话。已有 MC 匹配继续保留旧会话认领、标题和新终答回放。旧兼容集合和分页集合均在选页前应用归属条件。
2. [x] `mc_frame_history.py`、`main.py` 与现有归档变更调用方从普通保存和退出待写入中去掉未变化的归档 upsert。复用回合待写索引，并记录只改摘要的所有者。保留新归档的上一聊天、同时变更的多个所有者及失败待重试项。`mc_frame_navigation.py` 用批量读定位未读。`mc_frame_history.py` 把已读写入移到后台并遵循现有完成与退出语义。
3. [x] `mc_frame_answers.py` 用有界预览缓存复用未变的正文，保留选中项、代次、正文和现有显示过滤。`mc_frame_files.py` 将文件管理的公网路由校验和大文件复制移出 wx 回调。
4. [x] `tests/test_history_ui_automation.py` 及相关测试复评单聊天加载、批量隐藏、笔记、旧远程路径。在当前实际规模与夹具下，确有停顿且仍受新范围影响的调用做局部修改。记录证据不足或实机特定的问题，保留争渡播报协议。

### Acceptance Criteria

- 给定含旧终端导入、MC 原生关联及普通聊天的库，当从桌面历史、Ctrl 方向键和 MC 历史接口打开聊天时，只出现 MC 创建的聊天，Alt 和历史菜单无开关。日常数据无需删除或迁移。
- 给定多条未改动归档，当新建、发送或切换聊天并触发真实保存时，未改动归档零 upsert；编辑多个所有者后重开可读回对应结果。在真实延迟保存阶段测键盘回调。
- 给定同一批长答重复刷新、全部已读和被 SQLite 锁挡住的已读提交，当使用正常入口操作时，二次重绘不重复转换正文，未读定位使用有界查询，锁释放前 UI 可处理后续键盘事件。
- 给定文件路由慢或复制被门闩暂停，当桌面操作进行时，主循环先处理键盘，之后报告正确结果。

## Implementation Notes

上一轮仅改默认值的两文件已保存到 Git stash，本计划替代它。工作区内仅本计划和本阶段实现由本轮管理。共享工作区可能存在其他未提交工作，保留它们。本轮交付源码与隔离验证，日常 MC 的数据、安装目录及进程由用户继续控制。

- 本机数据库只读统计为 1,436 条，MC 集合保留 21 条，其中 20 条已有原生身份映射。直接调用过滤后的查询和五条一页的分页接口，覆盖完整 21 条，未输出聊天身份或正文。
- 独立 SQLite 探针播种 1,435 条 MC 自建归档。零改动时零 upsert，约 0.01 毫秒。改两条标题时恰好两次 upsert，约 15 毫秒，重新读取后正确。
- 独立预览探针的 20 条合成回答每条约 26,400 字符。首轮转换 20 次约 800 毫秒，次轮转换零次约 0.05 毫秒。只读复评保留的 21 条真实 MC 聊天，最长回答 2,343 字符，逐聊天首次转换峰值约 37 毫秒。保留首次正文生成语义，极长冷转换仍可能有开销。
- 本机笔记共 12 本、54 条，最大一本 21 条。单聊天完整回合读取、笔记和争渡驱动没有证据支撑本轮额外重构。旧远程集合和批量隐藏的输入已缩为 MC 所有者。
- 独立验证 7 项核心用例通过，包括混合历史、1,435 条归档实装保存、长答缓存、SQLite 写锁与按键、两条文件等待和关闭结算。补齐旧客户端缓存直接访问纯终端 ID 的读取、消息及新建保护后，混合历史与远程流程共 6 项再次通过。原基线同样复现 4 项既有失败。隔离环境缺 Codex/Kimi 可选运行时，3 项跳过；AGY 合成运行时回放通过。
- 首轮两条新增文件夹具未隔离目录，后续已显式使用测试目录并通过。用创建时间和已知夹具内容核对后，已清理默认目录中本轮产生的六个测试副本。

## Plan Change Log

## Review Triage Log

### 2026-11-27 — Review pass

- 盲审复核了旧版远程已读同步清单、未结算通知、当前聊天保存锁、保存按键测试、状态文件错误、文件复制关窗、文件通道并发、长答缓存展开、兼容历史重复和无关供应商枚举。
- 边界复核重复确认了通知已读权威清单、复制关窗、展开缓存和保存期间按键。其中另有一项是从回答发送文件时，复制完成后可能重新捕获选中项。
- 验证复核要求从保存开始投递按键，并从真实已读、关联关窗和最终批次贯穿验证。
- 意图复核确认 MC 历史和 RC 使用的 MC 历史集合遵循同一规则，旧库数据保留。实体争渡及 RC 客户端显示仍需目标环境验收。

- `[low]` `[defer]` `read_state_sync` 的旧终端权威条目仍存在。RC 用该流更新后台已读缓存，聊天列表独立从 history 集合读。此轮保留通知恢复历史。
- `[low]` `[defer]` 旧通知恢复中心仍可能返回已有纯终端终答。保留历史通知权威和检查点，聊天入口对旧 ID 返回不可用。
- `[medium]` `[patch]` 当前聊天延时保存被 SQLite 写锁占住，独立探针等待约 869 毫秒。缩短这条回调的写锁等待并重试。
- `[medium]` `[patch]` 延时保存测试原本在实际保存结束后才投递键盘事件。移动投递点并测耗时。
- `[low]` `[patch]` 延时回调不能观察旧 `_write_state_data` 吞掉的异常。将确认的状态文件写错误反馈到本地保存边界。
- `[medium]` `[patch]` 已接纳文件复制在进程退出前可能留下未完成文件。使用已有关闭生产者的后台等待边界排空。
- `[medium]` `[patch]` 新增并行文件发送可能重复启动同一公网通道。桌面准备和发送串行执行。
- `[medium]` `[patch]` 预览缓存容量小于用户主动展开的可见范围会出现连续全漏。以当前可见范围决定上限。
- `[low]` `[patch]` 无分页兼容列表可能重复返回启动后加载到归档的当前聊天。按当前 ID 去重。
- `[low]` `[defer]` 少数提供方的列表接口仍须枚举后按白名单筛选。纯终端不再写库或回调 UI，未实测此处仍有前台卡顿。
- `[low]` `[defer]` 边界复核的 `read_state_sync` 与盲审同一观察，保留旧通知恢复协议。
- `[medium]` `[patch]` 边界复核的复制关窗与盲审同一缺口，用关闭生产者等待测试验证。
- `[medium]` `[patch]` 复制再发送的路径会在用户等待期间重新捕获选中记录。保留开始时的选中项身份。
- `[medium]` `[patch]` 边界复核的展开预览缓存与盲审同一缺口，用大于 256 条的廉价夹具验证二次命中。
- `[medium]` `[patch]` 边界复核的保存按键测试与盲审同一缺口。
- `[medium]` `[patch]` 验证复核的保存按键测试与盲审同一缺口。
- `[medium]` `[patch]` 用例原先直接构造关闭批次，未覆盖实际关窗等待、收集并重试失败已读的过程。增加真实关闭路径的事件同步验证。

## Verification

- 运行相关真实 wx 自动化、ChatStore、读状态、共享会话/历史路由和文件流程测试。wx 串行，pytest 使用隔离数据库。对已知失败在基线对照，不扩大回归范围。
- 复用此前 1,435 归档和长 Markdown 的探针形态，报告写入数、回调顺序及读屏实机边界。记录所有新增测试的退出和临时文件清理情况。

## Auto Run Result

- 桌面历史、导航和 RC 历史集合只显示 MC 所有者，移除 Alt 与工具菜单的选项。纯终端保留原数据库记录并停止导入。旧缓存 ID 的直接读取、发送和新建均受到归属校验。
- `chat_store.py` 和 `mc_frame_native_history.py` 负责持久归属查询及原生候选预筛。`mc_frame_history.py`、`main.py` 和相关供应商回调负责增量保存、短等待重试和有序已读提交。`mc_frame_navigation.py` 批量定位未读。`mc_frame_answers.py` 复用包括展开页在内的正文预览。`mc_frame_files.py` 和 `file_transfer.py` 将桌面文件等待移出 wx，并在关闭时排空。`mc_frame_remote_routes.py` 与 `remote_nats.py` 保护集合和旧 ID。相应 README、远程说明和定向测试已更新。
- 审查后修补延迟写锁、状态文件失败反馈、关窗排空文件、发送串行和原始选择保护、快速接纳顺序、展开预览和兼容列表去重。增加保存开始时投递方向键、真实关窗等待已读的验证。既有通知权威缓存与供应商元数据枚举按上述原因延期。
- Director 审查补丁后的隔离定向回归 19 项通过。Engineer 在隔离 `DESKTOP_FILE_STORAGE_DIR` 和 pytest `basetemp` 下对 `tests/test_history_ui_automation.py`、`tests/test_file_manager_ui_unit.py` 和 `tests/test_main_history_storage.py` 串行复跑最后 13 项，13 通过，7.10 秒。普通与 Codex 保存均覆盖真实 SQLite 写锁期间的键盘事件与释放后保存，覆盖 1,435 条归档、300 条展开预览、文件等待、快速手机接纳及真实关窗已读重试。新增 RC 缓存保护与普通远程新建流程的此前独立复验 6 项通过。`git diff --check` 通过。
- 本轮修补多项中等问题。后续验收聚焦实体争渡与目标安装包，尤其核对同时有后台同步时 Tab、方向键及播报。Codex 与 Kimi 的真实可选本机环境用例未执行，AGY 合成原生回放通过。当前运行包未替换。
