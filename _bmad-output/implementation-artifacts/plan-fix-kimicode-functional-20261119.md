---
title: '修复 Kimi 连续发送、任务控制与断线恢复'
type: 'bugfix'
ticket: ''
created: '2026-11-19'
status: 'built'
route: 'full'
route_source: 'auto'
review: 'thorough'
review_source: 'auto'
lenses_ran: ['blind-hunter', 'edge-case-hunter', 'verification-gap', 'intent-alignment']
review_loop_iteration: 0
baseline_revision: '6c5d2cb8fd84a7a5d0ee15e7dd9f17ba4bff180c'
followup_review_recommended: false
context: ['{project-root}/docs/kimi-runtime.md', '{project-root}/_bmad-output/implementation-artifacts/review-kimicode-functional-20261119.md']
warnings: ['oversized']
deferred:
  - summary: >-
      服务端接受 POST 后进程在 owner 落库前硬退出。
    evidence: |-
      预存窗口，与已处理未知结果后的重载不同。完整覆盖涉及未发送 intent 的恢复。
    location: >-
      main.py:11690
    severity: medium
  - summary: >-
      服务端接受请求后可能返回缺 ID 或非法成功响应。
    evidence: |-
      已有 D2，需取得固定运行时的实际响应以及该请求进入 transcript 的证据。
    location: >-
      kimi_server_client.py:936
    severity: medium (unverified)
  - summary: >-
      WS 换代与已投影事件入队交错的可见影响。
    evidence: |-
      投递窗口存在，尚需正确 owner、代次与去重门禁下出现可见状态倒退的证据。
    location: >-
      kimi_server_client.py:1275
    severity: medium (unverified)
---

<intent-contract>

## Intent

修复审查 P1–P11，使 MC 在用户连续发送、切换聊天、等待模型交互或恢复长任务时，始终把输入、控制和结果交给正确的会话与提问。实时测试必须分别观察流式内容和真实中断终态。

## Boundaries & Constraints

保持 2.5.3 固定运行时、V2 当前连接 ACK、原有 V1 能力、持久身份和既有对话读取兼容。只在隔离环境运行测试。日常 MC、聊天笔记、共享认证不用于验证；不打包、部署或重启日常程序。D1 打包回滚及审查 D2–D11 的未证实情形保留原报告。三个旧测试的状态单列。

## I/O & Edge-Case Matrix

| 输入或时序 | 可接受结果 |
|---|---|
| A 首轮建会话期间提交 A 的追问并切到 B | A 只建一个 session；仅发送 A 的历史与附件；B 保持原状 |
| 活动、已结束、取消失败三种 stop | 只请求当前 owner；区分请求已受理、已取消、自然完成和失败 |
| pending/ended/replay 的 approval 或 question | 按 session/interaction ID 响应一次；过期/已解决的请求无法再作答 |
| 多 owner 恢复、REST 与 WS 交错、断线时 POST/steer 结果未知 | 每个 owner 有独立恢复机会；精确归属、终态及提示只结算一次，不自动重发 |

</intent-contract>

## Code Map

- `main.py` 的 `_run_kimi_turn_worker`、`_chat_target_for_request`、`_model_startup_request_is_current`、`_kimi_submission_lock` 和 owner 登记处理发送。切换后可见列表会变，归档 turn 仍可保持同一对象。沿用请求代次门禁。
- `kimi_server_client.py` 的 submit/abort/answer_approval、`_handle_ws_message`、`_consume_v2`、`reconcile_v2`、`_retire_v2_turn` 共享协议路径。`main.py` 的 stop、交互弹窗、恢复 worker 与 `_migrate_legacy_kimi_owner` 负责用户可见状态。复用 `CodexUserInputDialog` 可用的控件与键盘约定。
- 审查报告列出固定 exe 内的 producer 函数、临时复核脚本及行号，可把确定性交错直接迁移至正式测试。基线为 `6c5d2cb8fd84a7a5d0ee15e7dd9f17ba4bff180c`。

## Tasks & Acceptance

- [x] `tests/test_kimi_integration.py`、`tests/test_kimi_server_client_unit.py` 先建立失败用例。用真实工作线程与 Event 控制“首轮阻塞→追问等锁→切到 B→解锁”；让 REST 在 WS 遍历期间退休一项；用可控时间让一个 owner 等待正文而另一个已完成。隔离 ChatStore、临时附件、服务端及音效。
- [x] P1/P2，`main.py` 在同一聊天的稳定锁内重读 owner/session；在释放锁前发布可供下一次提交读取的身份。根据原 chat/turn 获取问题、附件与历史；不同聊天仍可并发，过时代次取消；保留 WS 先于 POST 回包时的归属。
- [x] P7/P8/P9，`kimi_server_client.py`、`main.py` 给 V2 请求预分配 provider 接受的 prompt_id，发送实际内容前记入 intent，结果未知时沿已有 owner/持久化路径保留精确身份，重启按 ID 只读恢复。验证服务端返回及早到事件，不猜文本、不自动补发。旧数据仍走已有迁移；已知 ID 优先于时间，区分“选中哪条”和“完整时间线判断 alias”，保留同文歧义门禁。
- [x] P3/P10，`kimi_server_client.py`、`main.py`、`tests/test_kimi_live_smoke.py` 对 V2 使用实际 HTTP prompt action 中断指定 prompt；捕捉该身份权威 cancelled 终态并区别 failed、completed。等待服务器期间保持 UI 可用；没有明确活动 prompt 时不得停止别的任务。V1 保留原分支。
- [x] P4，`kimi_server_client.py` 读取顶层 interaction 帧；`main.py` 区分审批/问题并绑定 session、interaction_id 和来源聊天。沿固定 schema 将批准/拒绝/取消或按问题 ID 的选项与自由文本送至 `/interactions/{id}`，支持服务端提供的单选、多选、其他及关闭问题。使用实际 provider 选项 ID。ended、重放与切换聊天不重复答复，错误反馈归属原请求；沿用现有可访问控件和焦点行为。
- [x] P5/P6，`main.py` 调整独立 owner 的恢复预算，首条的等待不得耗尽后续项的预算。`kimi_server_client.py` 让 transcript 遍历、退休和完成去重一致；网络请求与外部回调避开共享状态的临界区，保持重连代次检查。用上述交错测试覆盖，不以捕获 RuntimeError 掩盖丢事件。
- [x] P11，`tests/test_kimi_live_smoke.py` 独立断言提交对象的 WS assistant 增量及终态，再对照 REST；在错误 session/prompt、自然结束冒充取消、仅 REST 有答案、等待超时这四类输入下，测试断言应失败。

**Acceptance Criteria**

1. Given A、B 有可区分的历史和附件，When 首轮建会话期间连续发送并切换，Then A 两条输入命中同一个 provider session，捕获的提交仅含 A 的标记，聊天列表和数据库中结果归 A；B 未增加回答。
2. Given 已观察到归属当前任务的 started，When 输入 /stop，Then 服务器收到该 prompt 的取消操作，MC 显示对应的中断结果；自然结束或其他任务的终态不能通过取消验收，拒绝或失败有可见反馈。
3. Given 固定版本 pending interaction，When 使用键盘批准、拒绝、选择答案或关闭，Then 可读的问题/选项与提交体对应，匹配请求可继续或按取消结算；切换、重放及已结束事件不答错、不重复弹窗。
4. Given 同场存在慢 owner、已完成 owner 和其他聊天的流，When REST/WS 交错补齐，Then 慢项按自身证明结算，其他正确答案可见并落库，后续 WS 进度仍抵达，重复 WS/REST 只产生一次答案及成功提示。
5. Given 历史重建/带附件的 POST 丢响应或同文 steer 丢响应，When 客户端恢复（含持久 owner 重载），Then 找回原 prompt、区分 alias 与独立排队，精确旧 ID 不被时间覆盖；用户只看到应有的回答，提交次数没有增加。
6. Given 隔离的真实 2.5.3 会话，When 发送普通问题和中断活动长任务，Then 分别记录匹配的 WS 增量、最终正文及匹配取消终态。断言不能靠 REST 替代流或自然完成替代取消。

## Implementation Notes

- 发送锁以聊天为单位，清理后释放该锁所在的旧代次。Worker 在锁内按捕获的 turn 身份读取附件和历史，向下一次发送发布已建会话，并重查请求代次。
- 固定版 V2 通过客户端 prompt ID 提交。在 POST 前登记 intent，响应丢失后保存精确 owner，优先使用 transcript turn、明确队列身份及 user frame 的 `promptIds` 副证据。已知旧 ID 命中时跳过文本和时间猜测。候选 alias 独立恢复，原回答已结算时从本地权威结果建立引用，不重复通知。
- V2 中止发送指定 prompt 的 HTTP action，服务器受理与同 prompt 的 `cancelled` 终态分别显示。V1 使用原有取消分支。
- 顶层 interaction 用 session 和 interaction ID 关联，审批和问题各按固定 schema 提交至 interaction 路由。复用 wx 模态框和键盘默认按钮，问题使用原生单选、多选和自由文本控件。明确未接纳的回复可由应用菜单重试。结果未知时，客户端先读取该 interaction 的状态。只有服务端确认仍为 pending 才启用用户重试。
- 各恢复 owner 独立计时。客户端用已有生命周期锁串行处理 transcript 状态，REST 请求与事件投递在锁外执行。WS terminal 显式标明来源，供 live 断言和 UI 区分。

## Plan Change Log

## Review Triage Log

### 2026-11-19 — Review pass

- verdicts: 26 findings — high 1, medium 13, low 1, false 6, maybe-false 5
- findings:
  - [medium] [defer] B1 进程在服务端接纳 POST 后、回包登记前硬退出可丢 ID。旧版也在回包后持久化；P7 处理已观察到 POST 结果未知后的重载。提交前落库和未发送 intent 恢复是独立的既有窗口。
  - [maybe-false] [defer] B2 缺 ID 的成功响应不进入未决恢复；固定 2.5.3 没有已接纳却缺 ID 的生产形态证据。沿用报告 D2。
  - [medium] [patch] B3（A）REST journal/snapshot 补齐也标记 WS 增量及终态。已复现 seq1→3、仅 REST 有 completed 时输出 transcript.terminal；需要区分恢复内容来源并覆盖负向断言。
  - [maybe-false] [defer] B4 generation 检查到发出队列项间可换连接，但有稳定 prompt、退休和 GUI 去重；没有展示该时序造成错误的可见记录，待有实际倒退证据再补。
  - [false] [reject] B5 固定运行时 registerInteractionsRoutes 成功回执直接从 body.decision 生成审批 outcome，提问由 dismissed 位决定。其他客户端已结束返回错误，不会得到相反动作的成功回执。
  - [low] [reject] B6 丢失 ended 且用户一直停留时，过期弹窗未按时间自行关闭；用户可 Esc 退出，返回时已有过期检查。专门增加模态计时器与销毁管理的成本超过罕见残留显示影响。提交前有效性另见 E6。
  - [medium] [patch] B7（B）未知 POST 后第一次只读 GET 也失败，WS 保持连接且没有新 pending 时，原菜单不能再核实。固定服务端重连会生成新时间戳，但正常连接未必重放；允许菜单只读复查，确认 pending 后才可再次作答。
  - [medium] [patch] B8（C）POST 未知而 GET 已确认目标结果仍报告失败。生产 worker 的 500→approved 探针得到 ended 且仍调用错误报告；按确认结果反馈。
  - [medium] [patch] B9（C）成功重试或 ended 后保留 chat 的旧错误，返回聊天会重新提示失败。按 interaction 身份清理，保留其他未决项的提示。
  - [medium] [patch] B10（D）单选 Other 文本框在预设选项仍选中时可以输入，OK 优先取 radio，静默丢失文字。使输入、选择及提交值一致，补原生控件测试。
  - [maybe-false] [defer] E1 同 B2。成功 status 带非法 JSON 的已接纳响应也缺固定 producer 证据，保留原 D2。
  - [medium] [defer] E2 同 B1。未知回包已发生后的持久化已有测试，中途硬退出是原本就存在的不同故障窗口。
  - [high] [patch] E3（E）旧 worker 先通过身份检查，clear 后旧锁被退休，旧 worker 却能无条件写回旧 session。验证旧锁身份与发布 session 需与 clear 退休互斥；用事件控制该顺序，证明下一次发送不会使用旧 session。
  - [maybe-false] [defer] E4 同 B4。避免在共享锁内执行外部回调是合理约束；待取得真实可见性错误的证据。
  - [medium] [patch] E5（C）与 B8、B9 同一终态反馈问题；确认已成功或成功重试，不再保留或继续提示该项错误。
  - [medium] [patch] E6（F）按 OK 时未过期，但 worker 调度或 client.start 跨过截止时间，仍然提交。生产 worker 在已过期 responding 输入下调用 answer_interaction 一次；HTTP 前复核固定 expires_at，无需增加定时器。
  - [medium] [patch] E7（A）同 B3。补齐来源决定断言是否能作为独立 WS 证据。
  - [medium] [patch] G1（G）现有原生控件联测始终停在 chat-wire，删除切换时的显示回调仍可通过。增加 B 可见时 A 收请求、切回 A 才可回应并提交一次的联测。
  - [medium] [patch] G2（B/C）未知 POST 的 GET 夹具总是 pending。补已结束、查询无项或失败，断言真实菜单状态、错误提示与 POST 次数。
  - [maybe-false] [defer] G3 同 B2/E1。缺 ID 时的客户端分支已知，固定服务端返回形态未被证实。
  - [false] [reject] I1 把 D1 打包维护算入本次修复与原审查分组及用户确认不符。该已知维护问题留在原报告。
  - [false] [reject] I2 P1/P2 和 P5–P9 覆盖线程、SQLite 和投影等不同边界；静态审阅者指出范围限制，未展示行为偏差。P4 真实切换测试缺口已列 G1。
  - [false] [reject] I3 P3/P10 有固定运行时及源码 wx 证据。工程验证者未独立重跑云端，记录边界即足，无额外缺陷。
  - [false] [reject] I4 P4 未验收物理键盘和云端实际 pending；已有原生对话框、真实 HTTP 夹具与固定 producer 核对。没有宣称物理操作或云端续跑已验证。
  - [medium] [patch] I5（A）P11 断言不能排除 WS 接收函数内部经 REST 补齐，见 B3。原契约未要求把流中片段提前作为正式答案写入列表。
  - [false] [reject] I6 diff 本身无法证明派遣或本地提交过程，调度记录与已提交的完整基线另可核实，无需调整产品。

B、E、G、I 分别表示 blind-hunter、edge-case-hunter、verification-gap 和 intent-alignment；I1–I6 拆列描述性意见以说明验证边界。合并后七组局部修补，高 1、中 6。三组 defer，其中一组为预存的硬退出窗口，两组需要生产证据。无 intent_gap 或 bad_plan。本轮保持已验证的实现，补丁交同一 director；待 engineer 复验后补记最终结果。关于后续审查建议，先核对旧 worker 与 clear 退休的交错是否仍留可见风险，再由父协调员按实际补丁定稿。

## Verification

按任务从红到绿保留命令、失败断言、修复后输出。运行受影响的 Kimi client、event mapping、integration、Kimi wx 响应/F1 和 `tests/test_model_session_recovery_ui_automation.py` 相关用例；wx 测试串行，独立临时数据库。将本轮结果与报告内三项旧失败逐条比对，修复引入的新增失败。

用隔离源码 wx 与真实固定 runtime 完成普通发送和长任务取消；使用独立 HOME、工作区和已授权的隔离认证，确保测试不会轮换或覆盖日常 token。审批/问题至少使用按固定 producer 建立的帧、真实 wx 对话控件与受控 HTTP 服务验证提交/后续更新；若能在隔离服务端产生实际交互，另记结果。真实云端或原生界面条件不足时明确哪项未验收，不据替身宣称成功。清理本轮自建进程与可再生文件，保留必要的脱敏证据。director 实现并定向自验，engineer 独立只读验证。

### 实施验证记录

以下命令在源码仓库运行，wx 测试串行，使用项目 `.venv/Scripts/python.exe -m pytest`。各 wx 测试使用独立 ChatStore，附件和 HTTP 夹具均为本轮合成数据。

- 红灯组为 `test_overlapping_kimi_followup_uses_origin_chat_after_switch`、`test_distinct_kimi_recovery_owner_gets_own_deadline` 和 `test_v2_rest_retirement_during_ws_projection_keeps_other_streams`。改动前分别观察到追问另建会话、第二 owner 的剩余预算为零、REST 退休导致 WS 遍历抛出 `dictionary changed size during iteration`。三个测试修复后均通过。
- `--noconftest -o pythonpath=. tests/test_kimi_server_client_unit.py tests/test_kimi_event_mapping_unit.py -q`，223 项通过。覆盖错误 session/prompt、自然完成、只有 REST 正文、等待超时及 WS/REST 交错。更新一个原有断言，以精确验证 `cancelled` 与 `failed` 两种终态。
- `tests/test_kimi_integration.py -q -k "v2_wire_interaction or v2_stop_exact or distinct_kimi_recovery or overlapping_kimi or lost_wrapped_post or lost_same_text or known_kimi_prompt_id"`，13 项通过，随后新增交互重试联测验证。覆盖切换后 A 所有权及落库、取消受理和权威终态、原生 wx 控件与精确 HTTP 体、慢 owner 与已完成 owner 独立结算、附件和历史包装的提交响应丢失与 SQLite 重载，同文追问 alias 和独立队列、旧 ID 优先。
- 第一轮 `tests/test_kimi_integration.py -q` 为 178 通过、3 失败。该轮的失败与原审查逐项相同，位于旧工具流水期望 `test_kimi_tool_completion_updates_started_item_with_result_and_failure`、原始 event ID 断言 `test_kimi_completion_does_not_overwrite_reused_item_id_from_another_turn` 和旧工具摘要期望 `test_real_fixture_status_thinking_and_tool_result_produce_primary_chinese_steps`。
- `tests/test_kimi_ui_responsiveness_automation.py tests/test_kimi_f1_e2e.py -q` 两个文件 12 项全部通过。另单列原生 session recovery 的两个用例。工作树与断开工作树的基线提交均在测试模拟 V1 的 POST 录制阶段超时。test_kimi_actual_startup_exit_and_recovery_visible 位于 kimi-submit-after-recovery，test_kimi_clear_active_runtime_final_visible_after_switch 位于 kimi-old-active-submit。基线对照耗时 55.76s，临时工作树已移除。
- 使用固定 2.5.3、临时 HOME/config/workspace/数据库和平台独立 API 凭据。在临时 `config.toml` 中用 `api_key_env` 引用凭据，不复制日常 OAuth。设置 `KIMI_LIVE_TEST=1` 与 `KIMI_LIVE_HOME` 后，CLI 的 `test_live_prompt_roundtrip` 和 `test_live_abort` 两项通过（17.98s）。前者要求匹配 WS assistant 增量、WS 完整正文与独立 REST 相等，后者要求匹配 started、中断后的匹配 WS `cancelled` 与独立 REST `cancelled`。
- 同一隔离配置下，源码 wx `test_live_frame_sends_and_stops_exact_kimi_task` 通过（20.36s）。普通问题的 WS 增量、终态正文和 REST 一致，精确 `/stop` 受理、目标任务中断、REST `cancelled` 与 ChatStore 落库均通过。进程关闭成功。审批和问题由固定 producer 形态、原生 wx 与回环 HTTP 联测，无需使用日常认证。

- 异常重试联测使用 HTTP 应用级明确拒绝和 HTTP 500 两种输入。后一种依靠独立 GET 确认服务端仍 pending。两种路径的 pending 重放都不再弹窗，只有显式选择当前聊天应用菜单中的重试才提交。
- 审批/提问的物理键盘导航和云端实际交互仍未验收。回环测试操作原生选择控件和模态框按钮事件，核对与固定 schema 对应的实际 HTTP 请求。

### 审查补丁与最终独立验证

七组 patch 全部完成（高1、中6）：A 区分 REST 补齐与真实 WS 来源；B 未知交互允许手动只读确认；C 按交互 ID 清理已解决的错误并显示权威结果；D 自由文本同步 Other 选项；E clear 与旧锁身份检查、会话发布互斥；F 提交前再次核对到期时间；G 真实聊天切换路径与精确 HTTP 计数回归。逐条裁决表中的 patch 均已按这些根因落实，无需修改 intent-contract。

engineer 初次独立复验 223 client/mapping +15 定向 integration 通过。最终补丁独立复验 client 16 passed（0.13s）、wx integration 22 passed（16.46s），后者使用 `tests/test_kimi_integration.py -q -k "v2_wire_interaction or clear_between_generation or overlapping_kimi or clear_resend_rejects_old_submit_return or v2_stop_exact" --tb=short`。clear 用例走真实 `auto_resend_first=True`；HTTP 测试覆盖显式重试、未知只读确认、过期禁提交及聊天切换。`git diff --check` 通过。

最终来源补丁后，director 以独立环境 API 认证重跑真实 2.5.3：CLI 问答/取消 2 passed（9.03s），源码 wx 发送/精确取消 1 passed（10.83s）。脱敏日志和源码/runtime 哈希在 `%TEMP%/mc-kimi-functional-final-logs-06c7offn/`，engineer 独立读取并确认与最终文件一致，未重复运行云端。临时 HOME、工作区、数据库、服务已清理，无源码 runtime 残留，未复制或改写日常 OAuth。

## Auto Run Result

- Status: built。
- P1–P11 已实施：聊天内容/会话归属、精确取消、审批/问题及重试、独立恢复预算、REST/WS 同步与来源、丢响应身份恢复，以及严格实时/取消断言。
- 文件：`main.py` 修复 UI/owner/恢复；`kimi_server_client.py` 修复协议和共享状态；`tests/test_kimi_integration.py` 增加并发、控件/HTTP、持久化与切换回归；`tests/test_kimi_server_client_unit.py` 核对协议和来源反例；`tests/test_kimi_live_smoke.py` 严格区分 WS/REST、验证真实取消及源码 wx；本计划保存实施和审查证据。
- 审查共26条意见，逐项理由见 Review Triage Log。合并7组 patch（高1、中6）全部修补并独立验证；3组 defer 保留于唯一 frontmatter 列表；6条 false 和1条 low 的拒绝理由均已记录。
- Follow-up review recommendation: false。清理竞态和其余修补已有确定性交错、受控HTTP/来源反例及独立验证，没有可指名的新风险需要另轮审查。既存硬退出窗口和两项未证实边界继续 defer，不表示已修复。
- 仍有3项既有 integration 失败、2项已基线对照的原生 recovery fixture 超时。物理键盘/读屏、云端真实 pending interaction、新打包版验收未完成；本轮真实 wx 为源码运行。未打包、替换或重启日常 MC，未修改日常聊天/笔记。
- 已清理本轮临时认证、测试数据与基线工作树；保留必要的脱敏日志、哈希和原有失败/冻结证据。本地提交，不推送。

## Code Review

### 2026-11-19 · 904cda1 独立复审

0 decision-needed，10 patch，1 defer，5 rejected。详细触发条件、逐项裁决及原始证据见 [独立审查报告](review-kimicode-fresh-904cda1-20261119.md)。

- [ ] [Review][Patch] R1 普通 /clear 后迟到回包重新挂回旧 session [main.py:11712]
- [ ] [Review][Patch] R2 单选长选项在横向裁切 [main.py:1493]
- [ ] [Review][Patch] R3 关闭失效/清理的重试请求时清掉原错误 [main.py:16622]
- [ ] [Review][Patch] R4 HTTP 先确认的 ended 记录绕过保留上限 [main.py:16511]
- [ ] [Review][Patch] R5 多项重试时后登记的请求无法操作 [main.py:16565]
- [ ] [Review][Patch] R6 只读重查启动失败后恢复可操作状态 [main.py:16661]
- [ ] [Review][Patch] R7 归档聊天重载时丢弃无法执行的历史重试提示 [main.py:22799]
- [ ] [Review][Patch] R8 ended 后延迟错误回调仍发送远程提醒 [main.py:16695]
- [ ] [Review][Patch] R9 检查 V2 原生审批正文 [tests/test_kimi_integration.py:3922]
- [ ] [Review][Patch] R10 从聊天验证 /stop 遭拒的反馈 [tests/test_kimi_integration.py:4058]
- [x] [Review][Defer] D2 沿用固定版本缺 ID 的成功响应待证项 [kimi_server_client.py:936]，需配对的服务端成功响应与 transcript 记录。

**Rejected**

- B2（false）固定 producer 从已提交的 decision 生成成功回执。
- B3（false）首轮早到请求在提交落地回调后找到且可展示。
- B10（low）在 wx 再强制 WS 先到会误判 REST 先正确结算；独立 live_abort 已强制核对 WS。
- E6（low）同 B10，源码 wx 用例另验证了实际聊天与持久化终态。
- I（false，作为缺陷）原验收对模拟和现场边界已有明确记录。
