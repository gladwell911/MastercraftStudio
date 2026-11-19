---
title: '修复 Kimi 清理竞态和交互重试，补齐审批与取消验证'
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
followup_review_recommended: false
baseline_revision: 'd6c6327e003cb29252cfa3376c16ef9b3bcf6503'
context: ['{project-root}/_bmad-output/implementation-artifacts/review-kimicode-fresh-904cda1-20261119.md', '{project-root}/docs/kimi-runtime.md']
warnings: []
deferred:
  - summary: >-
      重试请求在聊天保持打开期间自然到期，菜单打开时仍会将其列为可重试。
    evidence: |-
      基线的菜单和状态栏同样不在静置到期时退休请求。隔离时钟从 100 到 102，截止时间 101，打开菜单仍启用；点击后才退休且不会提交。本轮 R3 的模态返回与 clear 路径已区分。
    location: >-
      main.py:2666, main.py:16604
    severity: medium
  - summary: >-
      已到期的 pending 帧先发远程操作提示，之后展示检查才识别到期。
    evidence: |-
      基线 _offer_kimi_interaction 已在显示前推送。隔离注入 expires_at=101、时钟 102 的帧记录到一条 session-fake-1/stale 提醒；本轮 R8 的目标是已结束后的延迟错误回调。
    location: >-
      main.py:16590
    severity: medium
---

<intent-contract>

## Intent

修复独立审查 R1–R8；补足 R9–R10 两个用户入口断言。清理会话后，新提问使用新会话。用户能看全选项、重查每个未决请求，结束后不会再被提示操作。

## Boundaries & Constraints

维持固定 Kimi 2.5.3、V1/V2、精确 owner 和重放去重。使用隔离 wx、临时 ChatStore 和本地 HTTP 测试。保留原聊天记录；不重新发送未知结果，不改 D2 等既有待证项，不打包、部署或操作日常 MC、笔记和认证。

## I/O & Edge-Case Matrix

| 场景 | 可观察结果 |
|---|---|
| 普通 /clear 与成功或未知 POST、延迟 UI 回调交错 | 旧请求不能恢复或覆盖当前会话 |
| 重查失败、重试中到期、多项未决 | 用户可继续查询其他项；终态只清自己的反馈 |
| ended 与 HTTP、错误通知顺序互换 | 终态记录有界，过时通知不会发送 |
| 请求在 A、用户在 B、随后重载 | A 的历史错误不会冒充可操作请求 |

</intent-contract>

## Code Map

- `main.py` 的 _run_kimi_turn_worker、_retire_kimi_chat_runtime、_apply_kimi_startup_state 共用每聊天锁身份及 owner 锁。R1 要覆盖回包发布与实际 UI 回调执行。
- `main.py` 的 CodexUserInputDialog._add_kimi_question_controls、_show_pending_kimi_interaction、_receive_kimi_interaction、_complete_kimi_interaction_attempt、_report_kimi_interaction_error 和 _switch_current_chat 负责 R2–R8。复用已有 session/interaction 键、重试菜单、终态窗口与反馈逻辑。
- `tests/test_kimi_integration.py` 复用隔离 frame、FakeKimiServerClient、running_kimi_control_server。审查报告给出每个探针的名称与原始输出。

## Tasks & Acceptance

- [x] `main.py` 修复 R1 的退休、登记和 UI 发布交错，保留新 owner。
- [x] `main.py` 修复 R2–R8 的原生布局、重试可达性、终态清理、有界保留、启动错误和提醒归属。
- [x] `tests/test_kimi_integration.py` 将确定性交错作为回归，并扩展现有审批和 /stop 测试。

1. 给定首轮正常/未知 POST 在途，用户在发送入口执行 /clear，分别放行回包、登记或延迟 UI 回调后，旧 session 不复活；新提问命中新 session，旧回调不能覆盖新 owner。核对两次实际提交及聊天中保留的清理记录。
2. 给定六个带说明的单选项，在默认 620×420 原生窗中最后一项可见或能纵向滚动到，选择后发送对应 ID。原生审批正文可读到 Shell、Run the local checks、pytest -q，提交目标与回复各准确一次（R2、R9）。
3. 给定同聊天有两个重试请求，第一项持续 uncertain，重复操作可查询第二项；客户端 Popen 抛 OSError 后仍可重查，未新增答复 POST（R5、R6）。
4. 给定错误已展示，在模态内到期或普通 /clear 后错误不再指向重试菜单，别项未决提示保留。A 的错误在 B 可见时写入归档，SQLite 重载后没有活请求即不提示重试；有效重放仍可操作（R3、R7）。
5. 给定 513 个先 HTTP 确认再收到 WS ended 的请求，近期终态至多 512 个，活请求保留。若已排队错误反馈在 ended 后执行，远程提醒调用次数为零；真实未决错误仍提醒（R4、R8）。
6. 给定已开始的精确 prompt，从聊天提交 /stop、服务端返回 aborted:false，实际 HTTP 请求一次，命令显示失败；原提问继续 pending 并可自然完成（R10）。

## Implementation Notes

- 普通 `/clear` 保留问题及已有回答，结算清理命令之前尚未完成的 Kimi 提问并保存脱离旧会话的元数据。相同聊天的提交锁身份也用于早到 WebSocket 事件认领和启动界面回调，避免旧记录在切换或重建时重新归属。
- Kimi 问答单选项采用原生单列，重试菜单轮流取当前会话的未决请求。HTTP、WebSocket、过期和清理共用终态保留逻辑。归档提示只在其对应请求仍存活时显示，远程错误提醒使用当前请求状态和错误检查。

## Plan Change Log

## Review Triage Log

### 2026-11-19 — Review pass

- verdicts: 14 findings — high 2, medium 7, low 3, false 2, maybe-false 0
- findings:
  - [high] [patch] B1 已受理 /clear、其 worker 尚未执行时，下一问仍向旧 session 提交。真实 UI 入口受控延迟产生同一 session 的两次 V2 POST；新 turn 留在 pending，旧 owner 经重建复活。于接收 /clear 时先完成本地退休与会话切换，并保留命令反馈。
  - [medium] [defer] B2 静置至过期后打开菜单仍提示可重试。隔离时钟已证实；基线菜单同样只在实际选择时才结算。这与已修的模态内到期、/clear 反馈分别记录。
  - [medium] [defer] B3 过期的入站 pending 先发远程提醒。以过期间隔 1 秒的帧记录到 action_required，基线已走同一路径；本次改动修补的是延迟错误回调。
  - [low] [reject] B4 批量 clear 会重复扫描当前交互以裁剪终态。常规终态窗口为 512，没有大量同时未决项导致可见停顿的生产证据，为此增加第二套顺序索引或单独批量路径不合算。
  - [medium] [patch] B5 启动 UI 回调持全局 owner 锁跨 SQLite 保存。隔离掉包路径中，停在 _save_state 的 100 ms 内，另一真实线程取无关聊天的提交锁始终受阻，保存返回后立即完成。保持发布顺序，缩短临界区，保存及呈现不持共享锁。
  - [false] [reject] B6 固定 V2 submit_prompt 已在返回前检查非空 ID，生产提交聊天与会话也已校验；空 ID 的客户端成功返回不能抵达该路径。有效输入下登记失败只能是退休，discard 会识别。固定 producer 非法应答仍属原 D2。
  - [low] [patch] B7 登记探针同线程重入，不能单独证明另一线程的锁交接。保留原覆盖，与 B1 合并补实际 worker 或等价受控调度的回归，核对新 session、旧回调及新 owner。
  - [low] [patch] B8 审批全文和 IsShownOnScreen 不能识别部分裁切。当前短夹具实测 146×52 在 604×381 可用区内；扩展原生测试，核对其几何与完整内容。
  - [high] [patch] E1 与 B1 同一根因，延迟 /clear worker 后已复现旧会话接收下一问，按 B1 的入口修补。
  - [medium] [patch] E2 单列后单个较长且有效的说明仍可能超宽。原生选项实测 1828 px，可视区 588 px，水平滚动率 0。复用现有滚动容器使完整文字可访问，保留选项 ID、纵向布局与选择。
  - [medium] [defer] E3 与 B2 相同，现有静置到期菜单在下次选择前没有重新结算；不为该旧边界增加计时器。
  - [medium] [patch] G1 归档重载测试在回到 A 前清空了活请求，未断言活错误应保留。隔离补查证实产品切回 A 时正确显示 still unresolved。扩展既有测试在清空前往返 A/B。
  - [medium] [patch] G2 现有裁剪测试按登记次序完成所有请求，无法区分最早登记与最早完成。让先登记的项最后完成，再验证其重放去重和最早完成项被裁剪。
  - [false] [reject] I 产品 diff 无法独自证明各阶段的代理身份与收尾顺序。实际派遣、技能渲染和先审后修有流程记录；收尾属于用户下一步，尚未声称完成。该观察未指出补丁行为偏差。

共 6 组 patch（高 1、中 4、低 1），2 组既有 defer，3 条 reject。patch 已交原 director，修后验证及最终状态由协调员记录。探针在临时目录 mc-kimi-auto-review-e4a3/test_auto.py。原计划的 intent-contract 无需修改。


## Verification

隔离 wx 套件串行执行。用项目 .venv 的 Python 运行新增回归节点、test_v2_wire_interaction_reaches_native_controls_and_exact_http、test_v2_stop_exact_prompt_http_and_cancelled_terminal、原 clear/resend 回归及 tests/test_kimi_ui_responsiveness_automation.py。执行 git diff --check。已有 live 记录仅说明当时的源码问答/取消；新修补以相应确定性测试为证。

在 `D:/code/sj` 串行运行以下命令，49 项通过，178 项未选中，耗时 27.45 秒。

```powershell
mc/.venv/Scripts/python.exe -m pytest mc/tests/test_kimi_integration.py mc/tests/test_kimi_ui_responsiveness_automation.py -q -k 'v2_wire_interaction or v2_stop_exact or clear_resend or generation_check_and_publish or new_and_clear or clear_removes or slash_clear_retires or retry_expiry_during_modal or http_first_kimi or retry_menu_reaches or archived_interaction_error or responsiveness_automation or promptless_early_event or early_event'
```

用例覆盖普通清理与成功或未知 POST 的回包、登记、早到 WebSocket 认领及延迟界面回调交错，核对了清理历史的 SQLite 读回。原生窗口验证第六个选项及对应 ID，审批正文、回复及精确目标，和 `/stop` 被拒后原问题的正常完成。其他新增用例覆盖重查启动失败、公平重试、模态过期、归档重载和重放，以及 513 个 HTTP 先到的终态与迟到错误提醒。上述测试使用临时 ChatStore、隔离 wx、受控响应和本地 HTTP。

`git diff --check` 通过。

### 最终审查补丁验证

六组合并补丁已全部完成：界面接纳普通 `/clear` 时同步退休旧会话，后台只持久化和回报；启动回调原子发布内存后在锁外保存和更新界面；Kimi 长选项局部启用原生横向滚动；原生审批正文增加可用区几何断言；归档错误回归先核仍有效提示再核重载清理；终态保留按完成先后验证长期 pending 最后完成仍抑制重放。

director 定向 40 passed（16.50s），engineer 独立复跑 40 passed、181 deselected（24.30s）。命令为 `tests/test_kimi_integration.py -q -k "v2_wire_interaction or clear_worker_start or startup_save or long_option or retry_expiry or archived_interaction_error or http_first_kimi or clear_resend or generation_check_and_publish or new_and_clear or clear_removes or slash_clear_retires or v2_stop_exact"`，具体执行节点以本轮代理验证记录为准。原生窗口及数据库均为隔离夹具，未运行云端或日常程序。最终 `git diff --check` 通过。

## Auto Run Result

- Status: built。
- 实施 R1–R10：普通清理的回包/早到 WS/延迟 UI/重建入口共享退休身份；界面接纳 clear 的边界立即退休，保留历史并结算旧 pending；重试提示、公平性、OS 启动错误、归档重载、轻量终态上限和提醒归属按已证反例修正；审批正文与 stop 被拒绝的外层状态有回归。
- 变更文件：`main.py` 为清理和交互行为；`tests/test_kimi_integration.py` 为确定性交错、原生控件、本地 HTTP、SQLite 及对应负例；本计划为实施与裁决证据。
- 审查14条意见逐项裁决见上文。合并6组 patch（高1、中4、低1）全部已修并独立验证；2组既存问题 defer；3条 reject 及理由保留。未证实原 D2 沿用原报告，不宣称解决。
- 独立验证：第一轮49项通过；补丁后实际影响范围40项通过，最后差异检查通过。真实键盘/读屏和云端 pending interaction 未新增验收，不将本地夹具称云端或 frozen 测试。
- Follow-up review recommendation: false。已用实际快速发送入口、确定性交错和几何/保留顺序断言覆盖此次修补，没有可指名的新未验证风险需另轮审查；两组既存延期问题仍保留。
- 未打包部署、重启日常 MC、使用日常认证或修改日常数据。验证临时进程结束，必要审查失败证据保留。下一阶段由用户指定 director 执行 neat-freak；本阶段仅本地提交，不推送。
