---
title: 'MC 聊天切换与争渡播报'
type: 'bugfix'
created: '2026-11-23'
status: 'done'
baseline_revision: '6d1bc18948c0fdbe24c314b40f611f4e9a3b37fc'
review_loop_iteration: 0
followup_review_recommended: true
context: []
warnings: []
deferred:
  - summary: >-
      共享读屏通道在外部朗读或 SDK 自动按键打断介入时的结算
    evidence: |-
      SDK 的状态与停止接口按通道工作。MC 内部语音反馈先取消待结算标题。需要由目标机器的真实争渡核对未进入 wx 的按键和外部朗读，才能判断会否截断标题后误移焦。
    location: >-
      mc_frame_navigation.py:_announce_chat_navigation
    severity: medium (unverified)
  - summary: >-
      并发历史标签更新或消息完成重排可能产生额外原生历史事件
    evidence: |-
      独立 WinEvent 探针对当前和固定基线均发现相同事件。它们由独立后台行变化触发，单独执行 Ctrl+↑/↓ 时已消除历史事件。
    location: >-
      mc_frame_history.py:_upsert_history_row
    severity: medium
  - summary: >-
      可见执行列表的会话归属切换可产生执行行选中事件
    evidence: |-
      基线和当前都会刷新可见执行列表以避免保留上一会话的执行正文。保留既有呈现规则，争渡在这种并发场景的实际朗读未测。
    location: >-
      mc_frame_answers.py:_render_answer_list
    severity: medium
  - summary: >-
      隔离交付候选包的实体争渡听感复验
    evidence: |-
      用户已接受独立原生列表原型。候选源码通过原生 WinEvent 和共享 SDK 客户端测试，需要带实体争渡复听新冻结包以核对最终音频。
    severity: medium (unverified)
---

<intent-contract>

## Intent

**问题**：Ctrl+↑/↓ 同步历史行时产生原生无障碍事件，争渡插播历史列表及选中状态。标题走独立通道，语速与语音输入上屏不同。

**方案**：把已接受的测试版行为合入 MC 源码。标题复用语音上屏的争渡通道。快捷键期间延迟历史高亮和回答列表更新，标题结束后定位原生回答列表的最后一条消息。

## Boundaries & Constraints

**保留**：原生 wx 回答列表、正常 Tab/Shift+Tab 和方向键、选择顺序、未读边界、连续切换接替和主动操作取消规则。初次从区外进入回答列表时允许争渡报「回答：列表」。日常 MC 的退出与重启由用户安排。

**范围**：保留已接受的独立测试包。自定义消息行及其逐项选中状态属于已撤回的试验。KimiCode 重新登录后已实测收到回复，本次无 Kimi 修复。

## I/O & Edge-Case Matrix

| 情况 | 输入或状态 | 期望 |
|---|---|---|
| 首次切换 | 输入框内按 Ctrl+↑/↓ | 唯一主动播报是目标标题。读完直接到回答列表末尾，保留正常的入焦提示。 |
| 连续切换 | 正在读标题，或已在回答列表 | 最新标题接替，只有最新会话可自动落焦。原生历史高亮延后到用户使用历史列表。 |
| 回到历史 | 切换后使用 Tab 或其他入口进入历史列表 | 同步已打开的会话，然后按原规则处理普通方向键和 Enter。 |
| 无法结算 | 空聊天、读屏不可用或主动打断 | 保留原控件的焦点或继续用户动作。后续仍可正常使用历史和回答。 |

</intent-contract>

## Code Map

- `main.py`、`mc_frame_voice.py`、`zdsr_tts.py` 的 `_zdsr_tts`、`_speak_text_via_screen_reader`、`speak_chat_navigation` 和 `speak_state` 提供实际复用点。语音失败可替换客户端，避免标题持有失效引用。SDK 状态等待及普通按键处理继续复用。
- `mc_frame_history.py` 的 `_navigate_history_chats`、`_show_history_chat`、`_refresh_history`、`_select_history_row_if_present` 和 `_activate_selected_history` 控制逻辑会话及原生选中项。关注快速连续切换、显式聊天操作和后台刷新。
- `mc_frame_navigation.py` 的 `_announce_chat_navigation`、`_cancel_chat_navigation_focus`、`_on_chat_navigation_child_focus` 控制语音和焦点生命周期。`mc_frame_answers.py` 已识别延迟更新的旧行。
- 只读参考 `D:/code/sj/_mc-ctrl-arrow-preview/source` 中当前恢复的原生列表试验。日常取证发现未聚焦的历史列表也会为 SetSelection 发送 MSAA 焦点事件。已确认的测试包是交互依据，生产实现应照常支持现有显式历史操作。

## Tasks & Acceptance

**任务**

- [x] `main.py`、`mc_frame_navigation.py`、必要的 `zdsr_tts.py` 修改：标题及其停止、状态查询与语音上屏使用同一常规客户端。
- [x] `mc_frame_navigation.py`、`mc_frame_history.py` 修改：避免切换引发原生历史选中事件，保留用户主动使用历史时的同步以及标题正常完成后的末尾定位。
- [x] `tests/test_desktop_navigation_ui_automation.py`、`tests/test_zdsr_tts_unit.py`：适配已有断言并覆盖新的播报与焦点边界。
- [x] `docs/voice-input.md`、`README.txt`：从用户入口说明当前行为。

**验收**

- 给定有文本的目标聊天，当在输入框、回答或历史使用 Ctrl+↑/↓ 时，原生 wx 路径在标题期间保留焦点，历史项不会因切换被重新选中。标题正常结束即定位末尾回答或未答问题。
- 给定语音上屏可以播报，当切换聊天时，标题使用相同的争渡客户端及通道，连续按键仍可接替。
- 给定完成切换或用户打断，当 Tab、Shift+Tab、普通上下键或 Enter 操作原生列表时，鼠标与键盘原有入口可用。重新进入历史时同步正确聊天。

## Spec Change Log

## Review Triage Log

### 2026-11-23 — Review pass

- verdicts: 17 findings — high 0, medium 5, low 5, false 3, maybe-false 4
- findings:
  - `[false]` `[reject]` Blind：应恢复标题专用的安装目录优先搜索。已接受的原型与语音上屏共用随包优先的客户端。旧文档承诺单列为文档缺陷。
  - `[maybe-false]` `[defer]` Blind：SDK 按键打断可能导致错误移焦。默认配置已确认，指向错误移焦的实际争渡事件序列待目标机确认。
  - `[maybe-false]` `[defer]` Blind：共用通道可能停止外部播报或误读外部空闲。应用内语音反馈先取消标题。系统外部事件的影响需真实争渡并发试验。
  - `[medium]` `[defer]` Blind：后台更新可能触发历史插播。独立探针当前与基线均复现后台历史行变化；快捷键单独执行已通过。
  - `[medium]` `[patch]` Blind：点击旧回答行后入焦刷新会错标新行。记录按下时的延迟状态，匹配的释放不会确认新行已读。
  - `[medium]` `[defer]` Blind：可见执行列表更新可能发选中事件。该更新在基线相同起点已经存在，保留可见执行归属及时更新。
  - `[maybe-false]` `[defer]` Edge：历史边界键或修饰键自动打断后可能误移焦。需要 SDK 声学与状态时序对照才能确认。
  - `[medium]` `[defer]` Edge：后台重排时原高亮被重建。当前与固定基线均通过同一 WinEvent 探针复现此独立变化。
  - `[false]` `[reject]` Edge：共用客户端选随包 SDK 是回归。语音上屏和用户确认的原型已经采用该路径，补齐文档说明。
  - `[low]` `[patch]` Edge：旧文档仍写安装 SDK 优先和禁用按键打断。已改成实际共享客户端的搜索顺序和默认配置。
  - `[low]` `[patch]` Verification：焦点一直在历史时，普通 Up 的起点缺少覆盖。补测不移焦便按普通 Up 及 Enter。
  - `[low]` `[patch]` Verification：历史列表空白点击的取消缺少覆盖。补测保持历史焦点点击空白后继续普通方向键。
  - `[medium]` `[patch]` Verification：远程新建仍需等旧标题轮询才刷新。远程入口转换前取消标题和延迟，并验证成功返回时新列表已更新。
  - `[low]` `[patch]` Verification：文档仍描述已移除的配置。已更新 `docs/voice-input.md`。
  - `[false]` `[reject]` Intent：单看调用语句可能存在更广任务范围。同一对话的最终清单确认两个待合入项；KimiCode 已恢复，其他试验已撤回。
  - `[maybe-false]` `[defer]` Intent：仅源码和替身不足以判定最终听感。另有独立原生 WinEvent 对照和用户已接受原型，新候选仍待实体争渡复听。
  - `[low]` `[patch]` Intent：文档仍承诺旧 SDK 查找和按键策略。已按实际代码修正。

## Verification

- 在仓库根目录串行执行 `.venv/Scripts/python.exe -m pytest -q -p no:faulthandler tests/test_desktop_navigation_ui_automation.py tests/test_zdsr_tts_unit.py tests/test_zdsr_tts_packaged_unit.py tests/test_main_voice.py`，核对范围内失败与基线。由 engineer 独立验证。
- 原生焦点事件与实体争渡听感分开记录。若准备冻结包，使用独立输出及隔离数据。保留日常进程与已经验收的测试包。

## Auto Run Result

状态为 `done`。Ctrl+↑/↓ 标题复用语音输入的争渡客户端。标题播报期间保留原焦点并延后历史选中项同步。标题正常结束后，使用原生回答列表定位最后一条回答或未答提问。保留当前测试包和正在运行的日常 MC。

- `main.py`、`zdsr_tts.py`、`mc_frame_navigation.py` 共用争渡通道，处理最新标题接替、等待和取消。
- `mc_frame_history.py`、`mc_frame_answers.py` 保留原生列表及延迟刷新。历史键盘操作回读目标会话。鼠标按下时仍显示旧行的点击不会误标刷新后的回答已读。
- `mc_frame_requests.py`、`mc_frame_remote_routes.py` 在新建聊天时取消旧标题与延迟投影。
- `tests/test_desktop_navigation_ui_automation.py`、`tests/test_main_remote_routes.py`、`tests/test_zdsr_tts_unit.py` 验证原生导航、已读保护、远程新建和共用通道。
- `README.txt`、`docs/voice-input.md` 更新入口、通道设置和正常交互说明。

审查归并后修复 2 项中等问题和 3 项低风险问题，分别为旧行鼠标释放、远程新建同步取消、共用 SDK 文档、同焦点方向键覆盖和空白点击覆盖。逐条判定及延期证据见上方审查记录。下一次现场复验应关注共享读屏通道受到外部播报或 SDK 自动按键打断时的焦点结算。

Engineer 独立复验规格套件，76 项通过。历史重排和全局热键注册测试替身的两项失败，与固定基线 `6d1bc18948c0fdbe24c314b40f611f4e9a3b37fc` 同名同断言。远程新建及关联入口的定向套件 10 项通过。隔离 WinEvent 对照证实，快捷键本身在标题期间没有原生历史与回答事件，结算仅进入原生回答列表，显式回到历史才同步选中项。探针使用模拟播报。

使用 `package_mc.ps1 -BuildOnly` 构建独立候选包，退出码 0。已核对打包产物的 10 个关键方法与源码一致，且包中没有试验版自定义列表模块。候选位于 `C:/Users/gaope/AppData/Local/Temp/mc-chat-shortcut-candidate-81e9fde2/dist/mc/mc.exe`，SHA-256 为 `7d9cbbab75a111b66aca595aba15e65c87c28cba0e4d18c636b3a8df08456a40`。本次未覆盖日常安装。候选包的实机听感待切包后复验。
