---
title: 'MC 聊天切换独立审查修复'
type: 'bugfix'
created: '2026-11-23'
status: 'done'
baseline_revision: 'c96c3c017eda2fc580c5d65e67222a1248a35a93'
review_loop_iteration: 0
followup_review_recommended: true
context: []
warnings: ['multiple-goals']
deferred:
  - summary: >-
      远程新建最后一次保存失败时的持久化事务边界
    evidence: |-
      固定基线已在最后一次保存前分配新会话并安排归档持久化。本次修复目标是失败后显示、交互和历史选择的归属一致性，保留原存储失败供调用方处理。
    location: >-
      mc_frame_remote_routes.py:_start_remote_new_chat
    severity: medium
  - summary: >-
      历史改名对话框期间目标被同步归档或删除
    evidence: |-
      固定基线已在打开模态对话框前取得目标对象，并在确认后直接使用它。本次按 ID 固定菜单目标，未改变模态保存的既有边界。
    location: >-
      mc_frame_history.py:_history_rename
    severity: medium
  - summary: >-
      文本消息完全处于已裁剪的较早页时的自动定位边界
    evidence: |-
      当前与基线都只在已投影的有限页面内选择末行。需构造足量非正文尾行并验证页投影，才能判定实体焦点影响。
    location: >-
      mc_frame_answers.py:_render_answer_list
    severity: medium (unverified)
---

<intent-contract>

## Intent

**问题**：独立 Dog 审查确认历史右键菜单、远程新建失败、回答鼠标手势和共用争渡停止权限存在四项回归，另有未答提问落到空行的既有验收缺口。

**方案**：让历史菜单、回答手势、异常恢复和争渡取消各自使用正确的操作归属，并在末尾以有正文的回答或未答提问定位。

## Boundaries & Constraints

保留原生回答列表、初次入焦允许播「回答：列表」、常规 Tab 和方向键、历史插播延迟、语音上屏共用通道。当前日常包、已接受的原型、真实数据和账号均保持原状。争渡 SDK 对系统外部播报的完整识别另待实体复听。

## I/O & Edge-Case Matrix

| 情况 | 触发 | 期望 |
|---|---|---|
| 历史右键 | 历史仍高亮 A，快捷键已切到 B，鼠标菜单指向 A | 菜单动作对 A。键盘菜单和 F2 同步后仍操作 B。 |
| 远程新建失败 | 原生回答显示 A，逻辑会话是 B，归档/保存失败 | 错误返回后仍显示与活动视图一致的回答，已读目标及历史同步保持同一会话。成功时立即更新。 |
| 鼠标手势 | 从旧行开始的点击或双击跨越切换与刷新 | 取消待定移焦，不用新聊天行解释旧手势。下一次独立点击仍可确认正确行。 |
| 暂未起声 | SDK 返回 9，用户按普通键或等到超时 | 标题未被 SDK 接受时不停止共用通道。已接受标题可被后续标题、应用普通键或语音上屏接替。 |
| 空白末尾 | 未答提问后存在空白回答占位 | 标题读完选择有正文的最后一条回答或未答提问。 |

</intent-contract>

## Code Map

- `mc_frame_history.py` 的 `_on_history_context`、`_show_history_menu`、历史动作入口、`_on_answer_mouse_down`、`_on_answer_mouse_read`、`_focus_latest_answer_immediately`。鼠标菜单的屏幕坐标应通过原生命中识别，键盘菜单单独同步；回答手势需要跨真实原生按下、刷新、双击和松开。
- `mc_frame_answers.py` 的 `_render_answer_list` 末尾选择、`_on_answer_activate`。资格判断需与实际聚焦保持一致；`main.py` 已绑定现有鼠标入口。
- `mc_frame_remote_routes.py:_start_remote_new_chat` 在可能抛错的归档和持久化前清掉延迟标志，核对 `_archive_active_session` 及本地新建相同交互。复用现有取消或恢复路径，不新建持久化协议。
- `mc_frame_navigation.py:_announce_chat_navigation/_cancel_chat_navigation_focus` 和 `zdsr_tts.py:speak_chat_navigation` 可区分等待、接受和错误。实证返回 9 时没有 `Speak` 调用，取消却调用 `StopSpeak`。
- Dog 的隔离原生事件、SQLite 写锁、SDK 超时复现及裁决位于 `C:/Users/gaope/AppData/Local/Temp/mc-dog-triage-be2436b04dfd4a8592fc753919b74180.md` 附索引。使用现存 wx 测试夹具，避免碰日常数据。

## Tasks & Acceptance

- [x] `main.py`、`mc_frame_history.py`、`mc_frame_answers.py` 修正右键目标、回答鼠标手势与正文末尾资格。
- [x] `mc_frame_remote_routes.py` 修正新建失败下的投影归属，继续验证成功和本地新建。
- [x] `mc_frame_navigation.py`、必要的 `zdsr_tts.py` 区分已接收标题与等待。复用上屏通道配置和现有轮询。
- [x] `tests/test_desktop_navigation_ui_automation.py`、`tests/test_main_remote_routes.py`、`tests/test_zdsr_tts_unit.py` 覆盖矩阵。完成新建、普通历史键盘同步及语音接替的定向回归。

**验收**

- 给定同一个用户手势或失败退出，当异步切换覆盖原生列表时，操作仍针对用户看到的会话和行，原生列表日常浏览行为保留。
- 给定标题与语音上屏共用通道，当标题未被接受或序列被替换时，播报取消与焦点归属可用受控 SDK 和原生 wx 入口观察。

## Spec Change Log

## Review Triage Log

### 2026-11-23 — Review pass

- verdicts: 17 findings — high 0, medium 10, low 5, false 1, maybe-false 1
- findings:
  - `[medium]` `[patch]` Blind：归档内部保存失败时已退出历史查看。恢复仍可用的原历史聊天并补归档内部保存故障用例。
  - `[medium]` `[defer]` Blind：远程新建末次保存失败可留下已分配会话。固定基线同样先分配再保存，事务策略延期。
  - `[medium]` `[patch]` Blind：二次投影异常可盖过原错误并跳过历史恢复。两项分别处理，原错误继续抛出，失败的回答投影维持保护。
  - `[medium]` `[patch]` Blind：SDK 等待替换标题时丢失旧标题的停止权限。保留已接受标题的权限到显式取消或结算。
  - `[medium]` `[patch]` Blind：同一聊天清空后重建同文行可误接受旧手势。清空当前可见聊天时撤销旧手势并测试活动与历史路径。
  - `[low]` `[patch]` Blind：右键空白处仍可能被旧标题移焦。取消标题并仅在命中有效聊天时打开菜单。
  - `[medium]` `[defer]` Blind：改名模态期间目标被外部改动。旧实现已持有跨对话框的对象，本次菜单固定 ID 与此边界独立。
  - `[maybe-false]` `[defer]` Blind：极端非正文尾行可能将最后正文挤出默认页。需要用实际可达数据和原生页面复现。
  - `[medium]` `[patch]` Edge：同一聊天同索引同文本的替换会匹配旧鼠标手势。清空时撤销，验证重新点击仍可读到新行。
  - `[medium]` `[defer]` Edge：改名对话框和外部归档并发。确认前后引用同一对象这一行为在基线就存在。
  - `[medium]` `[patch]` Edge：等待替换标题期间取消未停止旧标题。保留停止权限并测试 SDK 9 状态后 Home。
  - `[medium]` `[patch]` Edge：归档的内部保存失败后无法恢复先前历史视图。复用可查到的原历史目标重投影。
  - `[low]` `[patch]` Edge：右键历史空白处忽略待定移焦。补取消并确保空白不打开菜单。
  - `[low]` `[patch]` Edge：原故障用例只覆盖归档前预刷。新增归档已经改写视图后的保存故障覆盖。
  - `[low]` `[patch]` Verification：右键测试一直点击原来高亮行并使用替身动作。新增原生点中未高亮行后执行真实置顶操作的验证。
  - `[low]` `[patch]` Verification：等待 SDK 后转为接受再取消缺少覆盖。新增等待转接受后按 Home 的 SDK 停止计数。
  - `[false]` `[reject]` Intent：代码差异自身不能证明代理次序与收尾动作。派遣及技能按外部工作流执行，代码差异的交付面为应用修复。

## Verification

- 串行运行 `.venv/Scripts/python.exe -m pytest -q -p no:faulthandler tests/test_desktop_navigation_ui_automation.py tests/test_zdsr_tts_unit.py tests/test_zdsr_tts_packaged_unit.py tests/test_main_voice.py`。
- 串行运行 `.venv/Scripts/python.exe -m pytest -q -p no:faulthandler tests/test_main_remote_routes.py tests/test_main_requests.py tests/test_main_codex.py tests/test_main_chat_information.py -k 'remote_new_chat or new_chat_broadcasts_remote_history_changed'`。核对固定基线失败及实际通过的矩阵用例。

## Auto Run Result

状态为 `done`。按原生命中行固定历史菜单目标，键盘入口仍同步当前会话。远程新建失败时恢复仍可用的视图，并分别处理回答和历史投影。跨切换或清空的旧鼠标手势失效。等待中的争渡标题不独占共用通道，替换等待期间保留此前已接受标题的停止权限。末尾定位跳过空白回答，保留原生列表。

- `main.py`、`mc_frame_history.py`、`mc_frame_answers.py`、`mc_frame_requests.py` 处理历史菜单、答案手势归属和末尾消息选择。
- `mc_frame_remote_routes.py` 保持失败后的显示归属和原始错误。
- `mc_frame_navigation.py`、`zdsr_tts.py` 跟踪 SDK 已接受的标题。
- 三个受影响的测试文件覆盖矩阵，包括实际原生菜单、已读状态、SQLite 故障注入和共享通道。

复审补齐 4 组中等问题和 3 组低风险问题。延期 3 组、驳回 1 项，逐条证据见审查记录。实体争渡同时受到外部按键或外部播报打断时的听感仍待验证，因此建议在准备最终日常包时复听。

最终规格套件复跑 86 项通过、2 项失败，失败为与固定基线相同的历史重排和全局热键替身。Engineer 首轮因系统前台激活波动失败的原生用例在最终复跑中完成。远程及本地新建精筛 16 项通过；私有 Windows 桌面补充定向 10 项通过。复用了真实 SQLite 写锁及原生 Win32 事件探针。原样保留日常 MC、已验收测试包和用户数据。
