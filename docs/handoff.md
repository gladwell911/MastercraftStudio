# 当前交接

更新日期 2026-11-23。源码位于 `D:/code/sj/mc`，分支 `main` 跟踪 `origin/main`。本机日常进程的可执行文件为 `D:/code/cx/mc/mc.exe`。

## 2026-11-23 聊天切换与争渡播报

- `c96c3c0` 将已验收的原生列表试验合入源码。Ctrl+↑/↓ 的标题复用语音上屏的争渡通道。连续切换以当前打开会话为基准，最新标题正常结束才定位到最后一条有正文的回答或未答提问。返回或操作历史列表时再同步已打开的聊天。原生回答列表首次入焦可播报「回答：列表」，自动定位不确认已读。使用入口见 [语音与导航](voice-input.md#聊天标题播报)。
- `0bfe30d` 落实四层顺序独立审查确认的修复。鼠标历史菜单使用实际点中的聊天，远程新建失败时恢复可用视图，跨刷新或清空的旧回答手势失效。导航取消仅在 SDK 已接纳过当前切换序列的标题时停止共用通道。末尾定位跳过空白占位。审查裁决、故障注入和三项延期见 [初版规格](../_bmad-output/implementation-artifacts/spec-mc-chat-shortcut-screen-reader.md) 与 [审查修复规格](../_bmad-output/implementation-artifacts/spec-mc-chat-navigation-review-fixes.md)。
- 最终规格套件 86 项通过、2 项失败。`test_native_history_navigation_defers_current_completion_reorder` 和 `test_register_global_hotkey_registers_backslash_variants` 均在固定基线 `6d1bc189` 以同一断言复现。远程及本地新建定向 16 项通过，私有 Windows 桌面末轮定向 10 项通过。SQLite 写锁和原生 Win32 手势探针通过。模拟播报的 WinEvent 对照记录了标题期间和显式进入历史列表时的原生事件。
- 已验收的独立原生列表试验保留。`%TEMP%/mc-chat-shortcut-candidate-81e9fde2/dist/mc/mc.exe` 对应 `c96c3c0`。最终补丁 `0bfe30d` 尚未进入日常包。后续使用单独输出和隔离数据构建最终候选，在实体争渡中复听连续切换以及外部按键或播报打断后的焦点，再由用户安排日常进程退出和切换。

在 MC 根目录串行复现最终定向验证。

```powershell
.venv/Scripts/python.exe -m pytest -q -p no:faulthandler tests/test_desktop_navigation_ui_automation.py tests/test_zdsr_tts_unit.py tests/test_zdsr_tts_packaged_unit.py tests/test_main_voice.py
.venv/Scripts/python.exe -m pytest -q -p no:faulthandler tests/test_main_remote_routes.py tests/test_main_requests.py tests/test_main_codex.py tests/test_main_chat_information.py -k 'remote_new_chat or new_chat_broadcasts_remote_history_changed'
```

## 2026-11-23 本机 Kimi 2.6.3 与 Codex 代理

- `1380852` 接入本机 Kimi 2.6.3 Server API，保留 2.5.3 协议兼容。该源码构建使用用户安装的 CLI，配置与恢复入口见 [Kimi 运行时](kimi-runtime.md)。日常 KimiCode 2.6.3 重新登录后，从正在使用的桌面 MC 实际提交一次提问，约 4.5 秒收到 `KIMI_MC_OK_58D424AC`。聊天切换批次范围为桌面导航。
- 独立隔离验证中，真实 wx 聊天和审批 2 项通过，最终定向 43 项通过、3 项已知基线失败。隔离 `-BuildOnly` 退出 0，产物位于 `%TEMP%/mc-kimi-server-review-package/dist/mc`。实际冻结界面收到 `FROZEN_KIMI_REVIEW_OK_842` 并持久化为 `done`。见 [复审规格](../_bmad-output/implementation-artifacts/spec-kimi-server-review-fixes.md)。
- `e4f60b1` 接入 Codex OneDrive 代理。真实子进程回归覆盖两台机器的独立覆盖和配置缺失时的回退。见 [长期运行维护](runtime-maintenance.md#codex-代理)。
- 接续先用隔离历史样本验证 2.5.3 到 2.6.3 迁移和降级读取，再由用户安排日常切换，并在台式机及实体读屏环境验证。见 [迁移规格](../_bmad-output/implementation-artifacts/spec-local-kimi-cli.md)。

```powershell
.venv/Scripts/python.exe -m pytest -q tests/test_kimi_server_client_unit.py tests/test_codex_client_unit.py tests/test_packaging_specs.py
```

## 2026-11-23 双 Ctrl 与跨端回答无障碍

- `267429d` 起始批次修复 64 位双 Ctrl 钩子。RC 收到同源累计、六行聊天信息与可续页的最终回答。见 [语音与导航](voice-input.md) 和 [远程协议](remote-slim-protocol.md#usage-and-final-answers)。
- 当时的补丁独立复验 MC 10 项、RC 5 项通过。独立进程装卸了 x64 钩子，争渡安装目录的 x64 SDK 通过 WASAPI 回环验证首次起声、接替与停止。宽测失败在相应原基线复现。各项与对应实现见共享工作区 `D:/code/sj/_bmad-output/implementation-artifacts/plan-mc-rc-voice-answer-accessibility-20261123.md`。实体左右 Ctrl 与 Android TalkBack 仍需在目标构建中验收。

在 MC 根目录串行复现代表性用例：

```powershell
.venv/Scripts/python.exe -m pytest -q -p no:faulthandler tests/test_main_entry.py::test_global_ctrl_hook_preserves_hmodule_and_reports_native_error tests/test_chat_information_ui_automation.py::test_kimi_completion_usage_outlives_closed_information_observers tests/test_main_execution_ui.py::test_remote_execution_projection_page_uses_canonical_steps
```

## 2026-11-23 聊天交互、语音和退出

- Alt+A 先播放确认音，再按原聊天归属持久化清除和重发。已提交改名、后续回合与恢复身份在竞争中保留。语音输入保留设备尾段，最后发送一次终止帧。正常关闭先隐藏窗口并结算已接受的写入，持续存储故障恢复可访问窗口。实现入口见 [文档索引](README.md) 和 [关闭说明](runtime-maintenance.md#关闭与持久化)。
- 第二轮定向 43 项通过，独立复验 11 项通过。快速复查修复后键盘定向 2 项通过，独立复验 2 项通过。原生 wx 清理仍可输出基线 COM `0x8001010d` 诊断，定向运行退出码为 0。
- 早期隔离模拟器的 V2 NATS 手机清除证据对应当时源码。麦克风句尾及云识别、打包 MC 的实际 Alt+F4 退出待现场验收。各轮命令和模拟器版本见共享工作区 `_bmad-output/implementation-artifacts/` 下的 2026-11-23 交互主计划和复审修复计划。

在 MC 根目录复现最终键盘和代表性持久化、录音用例：

```powershell
.venv/Scripts/python.exe -m pytest -q -p no:faulthandler tests/test_main_history_storage.py::test_close_persists_other_rows_and_attention_after_retired_notification_conflict tests/test_kimi_ui_responsiveness_automation.py::test_close_persists_chat_while_notes_http_is_still_pending tests/test_notes_desktop_unit.py::test_notes_sync_close_detaches_network_and_waits_accepted_writes tests/test_voice_input_integration.py::test_stop_keeps_pcm_callback_still_buffered_in_device
```

## 2026-11-22 Kimi 累计、双 Ctrl 和 RC 分享

- Alt+Y 从可核验的 Kimi 2.5.3 终态历史恢复累计 token 和加权缓存命中率。未知字段及失效扫描保留各自状态。左右 Ctrl 同侧双击开始，短按停止，左侧追加到 MC，右侧写当前编辑位置。
- RC 附件按协商来源、目标聊天、发送 ID 和清空代次归属。隔离 Android 用真实 RC 确认按钮走 NATS/HTTP，第二份附件首次 PUT 返回 503，首次已受理提交回执丢失。重试后选中聊天新增一轮，其他聊天为零。Codex 构造器收到指令和两个 localImage，托管字节与源文件的 SHA-256 一致。模型服务器使用测试替身。
- 最终独立定向 30/30，通过真实隔离 Kimi 2.5.3、附件构造、HTTP、脱敏诊断及键盘回归。前一轮原生前台选区通过，最终轮记事本和 Edge 未取得焦点。命令范围与跨端结果见 [修复验证](../_bmad-output/implementation-artifacts/plan-fix-mc-rc-kimi-voice-share-review.md#auto-run-result)。
- 接续验收需要日常受影响 Kimi 聊天、物理 Ctrl/麦克风、实体微信和 TalkBack、两台实际在线电脑以及真实模型响应。管理员 PowerShell 无法执行这轮 package_mc.ps1 -BuildOnly，冻结包验收待在非管理员隔离目录完成。

## 2026-11-22 聊天用量、语音、附件与无障碍阅读

- Codex 与 KimiCode 显示原生用量和缓存命中率。累计行按当前会话归属更新，原文、同步和文件提取继续使用完整正文。语音按触发侧路由，OpenRouter Claude 在后台验证并发送实际附件。使用入口见 [README](../README.txt)。
- 独立定向矩阵 438 项通过，含 Alt+Y 与用量 106 项、原生长行 2 项。隔离 `RICHEDIT50W` 控件验证 9,800 与 28,000 字符均为可横向滚动的单行，Home、End、方向键、复制与提交保留尾部标记，真实换行仍形成两行。宽套件 14 项旧失败及排除的两项旧语音 UI 用例均有精确基线对照。wx 清理有 COM `0x8001010d` 诊断。
- 真实 OpenRouter Claude 请求在后续审查补丁前分别读回合成 PDF、PNG、TXT 标记。最终后台预检和历史附件重放已有本地覆盖。隔离 Kimi 2.5.3 通过空会话订阅 ACK 与上下文读取。命令、样本边界和 12 项审查修复见 [实施计划](../_bmad-output/implementation-artifacts/plan-mc-rc-chat-ux.md)。
- 该节保留 2026-11-22 的源码证据。同日附件和用量接续见「2026-11-22 Kimi 累计、双 Ctrl 和 RC 分享」。

## 2026-11-22 Kimi 键盘响应与执行历史

- Kimi 流式执行写入在后台按序完成。REST 整理消息并分批更新 wx，重新校验归属和撤回状态。可重试存储故障恢复前，正常关闭继续等待已接收的 REST 批次和写入。撤回保护涵盖重开的 SQLite 执行详情和远端交付。
- 独立审查落实 10 项修复并驳回 1 项。最终复验还修复了旧快照恢复后重新显示 31 个已撤回块的竞态。实际 wx 主循环中的 `CallLater` 续批和 SQLite 持有写锁期间均观测到原生焦点与选中事件，最后一条合法记录可从重开的临时 SQLite 读取。
- 独立串行宽套件 wx 82 项通过、2 项既有失败，Kimi 集成 154 项通过、3 项既有失败。wx 的原生回答查看器测试也复现基线 Windows COM `0x8001010d`。最后一次竞态修正后，Kimi wx 21 项、定向流式和 REST 37 项、SQLite 与 NATS 131 项通过。命令、基线项与审查证据见 [Kimi 键盘响应实施计划](../_bmad-output/implementation-artifacts/plan-kimi-keyboard-responsiveness.md)。
- 下一步在明确标识的目标构建，以相同控件和同一争渡环境对照空闲、Kimi 流式及 Codex 流式。记录物理按键到焦点和实际出声的时延，并验证真实云端。日常程序退出和重启由用户安排，切包仍须核对数据路径。

## 2026-11-20 至 2026-11-21 需求修复与代码审查

`969a145` 保存本阶段 MC 侧 20 个文件。内容包括中文文件名传输、OneDrive 机器私有配置恢复、独立笔记窗口、剪贴板导出删除、Alt+A 音效和附件失败门禁。联调背景与旧审查裁决分别保存在工作区上层 `docs/rc-mc-remediation-plan.md` 和 `_bmad-output/plan-fix-code-review-2026-11-21.md`。

- 四镜头审查落实 31 项修复。quick 复审 11 项中 8 项再修复、2 项驳回，`sharedFilesChanged` 链路留在 RC 交接。
- `test_notes_edit_window_unit.py`、`test_remote_machine_config_restore.py`、`test_file_transfer_unit.py` 共 55 项通过。中文文件名另实跑 8 例，机器恢复验证优先级与身份不匹配。`overlapping_starts` 和 `detail_view_keeps_history_tab_position` 仍是对应宽套件的基线失败。
- 笔记存储、远程加固和 wx 入口也已隔离复验。附件验收和 UI 自动化共 5 项通过，已读与通知共 26 项通过。`test_main_requests.py` 补齐测试替身后 80 项通过，剩余 16 项既有失败。`test_main_navigation.py` 的 8 项失败与当时基线一致。实体读屏、物理 Alt+A 按键、双机 OneDrive 恢复、真实提供方附件读取仍待验收。

## 职责拆分与验收

- `main.py` 从 24,850 行缩至 1,494 行。保留 `ChatFrame` 的 966 个原有方法，其中 937 个迁至 16 个职责类。`chat_store.py` 从 2,731 行缩至 919 行，`kimi_server_client.py` 从 2,348 行缩至 690 行。入口减量来自迁移，三个模块组最终净增 738 个物理行。查找入口和测试见 [文档索引](README.md#职责与测试入口)。
- 两份原测试的 880 和 141 个定义、3,036 个非实时参数实例全部保留。最终补丁加 5 个入口实例，共收集 3,041 项。拆分前后定向对照 2,078 个原实例，1,941 通过，136 在相同节点失败，1 项两版均触发 Windows COM 阻塞。随后 34 项修复复验通过，包含原阻塞的忙碌发送用例。最终补丁单独复验通知 17 项和入口 5 项，均通过。全量非实时套件未完成。命令和边界见 [整轮拆分计划](../_bmad-output/implementation-artifacts/plan-mc-code-split.md)。
- 已用实际 VS Code Python Debugger 运行源码 worker。正式 `-BuildOnly` 隔离构建的基线和最终版均验证了 `mc.exe` 和 `mc_worker.exe`。真实 Kimi 2.5.3 经回环服务完成连续两问，核对同一 chat/session、不同 prompt、原生界面与 SQLite/REST 正文一致，以及重启恢复。真实云端、物理键盘和实体读屏待现场验收。两项独立复审修复见 [修复计划](../_bmad-output/implementation-artifacts/plan-mc-code-split-review-fixes.md)。
- 早期直接构造 frame 的测试可能初始化源码笔记库。其修改时间落在首轮测试期间，只读 `quick_check` 通过，具体内容变化无法确认。补上自动临时笔记路径夹具后，定向复跑前后文件元数据一致。具体纠正见 [反思](reflection.md)。

## 首批废弃代码清理

- `beea5c1` 已完成。删除 `main.py` 中被覆盖的回答详情定义和 `notes_store.py` 末尾的 7 个旧远程操作方法，净减 267 行。回答详情键盘入口、模型过滤和笔记远程事务均通过源码核对。
- 串行基线与复测中，回答组 6 项和笔记组 31 项均通过。独立 quick 审查复跑通过，另有普通 Enter 字符钩子和旧端笔记位置兼容 2 项通过。实体读屏、打包和部署留待后续验收。命令与审查记录见 [首批清理计划](../_bmad-output/implementation-artifacts/plan-remove-obsolete-answer-and-notes-methods.md)。
- `notes_store_impl.py` 的去留留待独立核对。

## Kimi 源码与运行时

- `8d896e8` 增加固定运行时收集和暂存、最终包门禁。源码及日常文件 SHA256 已回读一致，2026-11-18 独立版本探针均为 2.5.3。当天以非管理员身份完成隔离默认 spec 构建，日常进程保持运行。当时的准备命令、包内路径和构建证据见 [打包修复计划](../_bmad-output/implementation-artifacts/plan-fix-kimi-runtime-packaging-20261118.md)。
- `904cda1` 落实 P1–P11 和七组审查补丁，涵盖原聊天归属、精确 prompt 中断、交互答复与只读重查、不同 owner 的独立恢复预算、未决 POST 和 WS/REST 来源区分。协议和验证命令见 [功能修复计划](../_bmad-output/implementation-artifacts/plan-fix-kimicode-functional-20261119.md)。
- `dec061a` 落实全新审查的八项产品修复及两项验证补强。普通 `/clear` 在聊天发送入口退休旧会话，保留历史并结算旧提问。旧回包、早到 WS 认领、延迟界面回调和归属重建均受原会话身份约束。交互使用原生单列控件，长选项可横向滚动。多个未决请求可轮流重查，启动失败仍可再次查询。归档重载只显示有效的重试提示，终态按完成顺序保留最近 512 项。审批可视正文和 `/stop` 被拒后的原请求状态均有回归。见 [独立审查](../_bmad-output/implementation-artifacts/review-kimicode-fresh-904cda1-20261119.md) 和 [最终修复计划](../_bmad-output/implementation-artifacts/plan-fix-kimicode-fresh-904cda1-20261119.md)。

## Kimi 验证范围

| 验证对象 | 结果 |
|---|---|
| 当时的隔离 frozen MC | 首问 5.97 秒完成，原生列表和 SQLite 可见。第二问完成且同聊天、同 provider session、不同 prompt ID，逐字复述断言失败。私有正文已清理，不能凭该断言定位原因。[原功能审查](../_bmad-output/implementation-artifacts/review-kimicode-functional-20261119.md) |
| `904cda1` 的源码与真实 2.5.3 | 首轮独立 223 项客户端及映射、15 项集成通过，最终补丁独立 16 项客户端、22 项 wx 集成通过。独立 API 认证下的真实 CLI 问答和取消 2 项通过（9.03 秒），源码 wx 问答及精确 `/stop` 1 项通过（10.83 秒）。CLI 登录后来恢复，最终云端复验使用独立 API 环境。脱敏日志 `live-cli.txt`、`live-wx.txt` 和 `source-sha256.txt` 位于 `%TEMP%/mc-kimi-functional-final-logs-06c7offn/`，已独立核对哈希。[验证记录](../_bmad-output/implementation-artifacts/plan-fix-kimicode-functional-20261119.md#审查补丁与最终独立验证) |
| `dec061a` 的源码 | 首轮 49 项通过，包含 wx 响应性套件。审查补丁后独立定向 40 项通过、181 项未选中（24.30 秒）。原生 wx、临时 SQLite、回环 HTTP 和确定性交错覆盖普通清理、审批/问答与取消，精确命令见 [最终修复计划](../_bmad-output/implementation-artifacts/plan-fix-kimicode-fresh-904cda1-20261119.md#最终审查补丁验证)。 |
| 旧失败基线 | 3 项旧 integration 断言失败和 2 项模拟 V1 提交超时均与基线一致，继续单列。[基线对照](../_bmad-output/implementation-artifacts/plan-fix-kimicode-functional-20261119.md#实施验证记录) |

2026-11-18 的 [steered REST 验证](../_bmad-output/implementation-artifacts/plan-kimi-steer-rest-recovery-20261118.md) 仍是活动长任务追问期间断连、新代 ACK、终答与 REST 核对、重放去重的现场证据，独立定向 159 项仅对应当时版本。65.39 秒场景允许 WS 补终结。脱敏证据在 `%TEMP%/mc-kimi-live-e2e-6f43394c04d746efb29e429d78efff4a/`。同目录根的旧失败探针和冻结 diff 保留用于复核。

## Kimi 接续

1. 拆分后的冻结包已通过回环服务连续问答。日常切换需先核对部署路径和聊天、笔记数据归属，应用退出和重启由用户安排。物理键盘、读屏、声学出声、日常环境的云端审批与长时运行仍待现场验收。
2. 继续保留纯 REST 自动调度、真实未决 POST 的整体恢复、完成身份淘汰后收到旧重放等验证边界。固定版本的 steered 末步、journal 覆盖和权威 reset 等待证形态见 [steered 后续审查](../_bmad-output/implementation-artifacts/review-kimi-steer-rest-followup-20261118.md) 和 [稳定性审查](../_bmad-output/implementation-artifacts/review-kimi-stability-order-sound-20261118.md)。
3. [功能修复计划](../_bmad-output/implementation-artifacts/plan-fix-kimicode-functional-20261119.md) 保留中途硬退出、有成功状态但缺失 prompt ID 或响应畸形，以及代次切换后旧队列项的三组延期项。[最终修复计划](../_bmad-output/implementation-artifacts/plan-fix-kimicode-fresh-904cda1-20261119.md) 另保留静置过期后重试菜单暂时可用和入站过期 pending 会触发远程提醒两项既有问题。安装复制中途失败时缺乏回滚独立留在 [原功能审查](../_bmad-output/implementation-artifacts/review-kimicode-functional-20261119.md)。

隔离测试环境的临时认证、服务和数据已按原验收清理。审查探针、冻结 diff、脱敏输出及已有构建交付物留在本机。

2026-11-10 的语音阶段成果及待现场验收仍有效，保留如下；与 Kimi 本轮软件验证分别核对。

## 语音输入：已完成与证据

- 2.0 升级 `8f16036`：默认资源为 `volc.seedasr.sauc.duration`，默认 WebSocket 路径为 `/api/v3/sauc/bigmodel_async`，`model_name=bigmodel` 不变；显式环境覆盖保留，资源与接口须配套。现有凭据配合 `rc/rtjava/whoareyou.wav` 经真实 `RealtimeAsrClient` 返回“你是谁？你叫什么名字？”，`terminal_sent/final_received=true`，`cleanup_error` 为空。旧 `/bigmodel` 路径配 2.0 资源实测 HTTP 400，不应只换资源而保留旧默认路径。独立 quick 审查无发现；固定样本成功不能替代麦克风、物理双 Ctrl 与焦点插入验收。RC 未修改或重新部署。
- 实现 `2a089f22f459b4ed465f4d83195c98645e57977c`：设备启动且首帧入队后提示就绪，ASR 后台并行连接；单一有界 FIFO 保留并顺序发送连接前后音频。GUI 不等待设备或网络；会话代次隔离旧音频、结果和排队提示。已修终包发送返回前 final 到达的竞争，以及重复失败取消导致清理尚未结束就收敛的竞争；诊断记录保留后续 GUI 投递时间。
- 新 dog 审查 `71e05f5d83ae27bf6166914f58421c352beafec5`：四名全新 reviewer 逐个执行，未确认新产品缺陷；1 个 medium 回归缺口 V1 已补完。审查仍有局限：edge 空数组按 workflow 记 failed layer，blind 数量未达 floor，不能称完整 clean review。原裁决保存在 [审查报告](../_bmad-output/implementation-artifacts/review-voice-local-first-new-dog-20261110.md)。
- V1 补强 `22284df21b428800384d32de74450a4980c93ad4`：真实 receiver→controller→converge 每代处理两个不同累计 interim 和不同 final，仅提交完整 final 一次；direct/optimize 及两代相同 final 均覆盖。拼接/仅保留首版两类纯内存变异各触发 2 项预期失败；原实现通过。president quick 审查 0 项问题，engineer 独立定向复验 2 passed（0.06s），并核对变异设计和默认 fixture 未改变。

2026-11-10 的历史软件验证记录见 [独立验证](../_bmad-output/implementation-artifacts/verify-voice-input-local-first.md) 和 [V1 局部计划](../_bmad-output/implementation-artifacts/plan-v1-voice-cumulative-transcript-regression.md)。

```powershell
.venv/Scripts/python.exe -m pytest tests/test_voice_controller_unit.py tests/test_realtime_asr_unit.py tests/test_voice_input_integration.py -q
# 42 passed；2.0 默认资源与接口回归新增1项。
.venv/Scripts/python.exe -m pytest tests/test_main_unit.py -k voice -q
# 历史入口，拆分前 17 passed, 982 deselected。
.venv/Scripts/python.exe -m pytest tests/test_voice_input_ui_automation.py tests/test_voice_input_e2e.py -k "local_ready or queued_ready or optimize_worker or software_ready or actual_capture" -q
# 串行：9 passed, 37 deselected。
```

当前桌面语音测试入口为 `.venv/Scripts/python.exe -m pytest -q tests/test_main_voice.py`。

两个完整 wx 文件另有 26 项旧失败，已逐条核对与原 HEAD 基线相同（模型列表、聊天箭头、笔记 Alt-X）；不计为通过，也未借收尾扩修。必要失败证据仍保留在系统 Temp 的 `mc-voice-baseline-6is0yu3m/`，不提交原始日志、录音、数据库或凭据。

## 语音：仍待验收与下一步

以下五项尚未证实，不能从软件回归通过推导为已解决：

1. 实际设备 stop/close 报错后的未释放可达性；现有 fake 模拟已释放后抛错，不能保证任意驱动都及时回收。
2. 真实 transport 的取消硬截止；只有取消抵抗 fake 曾超预算，实际 aiohttp 等效问题未复现，不新增推测性隔离机制。
3. 物理全局按键到声学出声 P95；先前隔离真实设备 ready 回调 P95=250ms、最小 wx 消息响应 P95=15ms 只是元数据，不能当作音效 API 或声学验收。
4. 真实云端慢连接下句首识别；2.0 固定音频已通过正常连接的真实云端识别，但未制造慢连接并验证真实麦克风句首，固定文本/编号 PCM 替身仍只证明字节顺序与 GUI 边界。
5. 4–8 小时后的同组复测及原小时级变慢机制；短硬件样本和100周期测试不代表长稳。

下一步若继续现场验收，按 [原实施计划](../_bmad-output/implementation-artifacts/plan-voice-input-local-first.md) 的隔离输入承接、对齐回环声学证据和长期复测执行。不要用提前播提示、重置启动缓存、丢帧或文本内容去重替代会话完整性。部署须另按既有流程授权，日常 MC 的退出/重启由用户操作。

## 其他仍有效的接续事项

- **文件下载与入口迁移：** 2026-11-02 实体97,677,556字节APK完整SHA256验证通过，安装未确认；约61.6 KB/s瓶颈未知。非空显式地址与 laptop 规则保留；`18221e2` 空白 DOMAIN 补丁已有源码验证，日常安装包是否包含仍待核实。继续调查时固定同一文件/来源分段测量，不扩查其他聊天或凭据。实际旧进程PID不可复用。
- **通知与维护：** 2026-10-26 日常包和 RC 的 feed/聊天库身份已核对，真实发布日志与独立后台/锁屏测试有证据；日常完整锁屏、点击及 TalkBack 验收未完成，MIUI 强停是独立条件。不得清 posted/terminal/已读账本。维护依据 [runtime-maintenance](runtime-maintenance.md)，保留权威事实与用户数据；24–72小时/月级稳定性仍无结论。
- **台式机 FRP：** 2026-10-30 两个 supervisor 安装、所有权/恢复/维护状态回归及当时 PONG/TLS 验证通过；按 [relay 入口](../deploy/mc-relay/README.md) 操作，不凭历史PID认领进程。ControlTower API 的本地18679拒绝连接/公网502、手机未连接ADB及未做重启可用性验收仍未解决；本轮不恢复服务或改网络。
- **其他既有问题：** token-only app_state 保存和 Windows 并发 replace WinError5 未修；通知/远程旧TLS/发送/详情延迟、两项历史 remote-slim 失败不计通过。源码笔记路径与打包版 OneDrive 数据衔接仍按项目指令与 [远程配置](remote-control.md)，不清日常数据。

下载/通知/FRP 的原始时间点、完整命令、私有证据位置和其他旧验证结果保存在 [交接前快照](archive/handoff-before-voice-local-first-2026-11-10.md)。该快照只证明对应历史版本；当前任务入口以上文为准。长期方法与真实纠错分别见 [experience](experience.md)、[reflection](reflection.md)。
