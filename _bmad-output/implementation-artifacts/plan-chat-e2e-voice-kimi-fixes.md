---
title: '修复端到端测试发现的语音写入和 Kimi 历史用量'
status: built
route: oneshot
review: 'quick'
review_source: 'pinned'
lenses_ran: [quick]
followup_review_recommended: false
baseline_revision: 12518bb32f21a00074c32599ce84e63eb2975380
context:
  - AGENTS.md
---

<intent-contract>

## Intent

修复定向端到端测试确认的 Windows 外部编辑器右 Ctrl 写入和 KimiCode 2.5.3 非空会话重启后 Alt+Y 累计用量恢复。保留左 Ctrl 的后台写入和关联会话的归属约束。Android 已在模拟器通过，不改手机产品代码，也不再连接或更改测试用 Android AVD。

</intent-contract>

## Tasks & Acceptance

- `windows_voice_target.py` 使用确实支持 `IUIAutomation2` 的 Windows COM coclass 初始化 UIA，并保留现有超时和焦点、选区回读保护。`mc_frame_voice.py` 用已有安全读取约定处理正常实例未初始化 `_closing` 的情况，关闭或录音代次过期仍禁止写入。给定真实隔离记事本、浏览器内光标或选区，右 Ctrl 合成识别结果写入精确位置，未启动物理麦克风或云端 ASR 的部分明确记录。左 Ctrl 仍追加两行到后台 MC，外部前台和选区保持。
- `kimi_server_v2.py` 的已结束回合在 `turn.usage` 为 null 而持久化步骤含完整用量时，以步骤还原回合总量和缓存计数。完整 `turn.usage` 继续优先，不能叠加。若一个实际消耗步骤缺必需数值或仅部分已知，不把该回合报告成一个看似精确的总量。保留最近一次调用、会话归属、无重复计数和未知值语义。给定真实隔离 Kimi 2.5.3 与本地合成 provider，关闭进程后从历史打开 Alt+Y，累计 110，最近与累计缓存命中率各 20%，无需再发模型请求。
- 保留本轮有复现价值的 opt-in 用例和测试总结。不得暂存原有私人 `AGENTS.md` 改动，不推送，不替换日常运行版。

## Verification

- 在 `D:/code/sj/mc` 运行新建 `tests/test_voice_native_foreground_e2e.py`，以 `MC_NATIVE_VOICE_TEST=1` 启用。用临时自建的记事本或浏览器窗口，按测试守卫逐个验证目标。跑新建 `tests/test_kimi_runtime_usage_e2e.py`，以 `MC_KIMI_RUNTIME_TEST=1` 启用。串行跑原生 GUI 用例。
- 跑受影响的 Kimi V2 和语音单元或已有界面测试。区分历史基线已知失败。`git diff --check`。

## Implementation Notes

复现为 CUIAutomation 的 `E_NOINTERFACE`、新会话实例缺少 `_closing`、真实 Kimi 历史重启后 `turn.usage=null` 且已完成步骤含 80 非缓存输入、20 缓存读取、0 写缓存和 10 输出。

- Windows 目标构造直接使用 `CUIAutomation8` 的 `IUIAutomation2`，保留两个 200 ms 的 COM 超时。写入前及结果回调沿用关闭状态和录音代次门禁。
- Kimi 只在终态回合缺少 `turn.usage` 时读取各终态 step。任一步缺字段、仍未终结或步骤为空时，累计保持未知。完整 turn 汇总优先，最近缓存命中继续读取最近完成 step，分页会话与去重逻辑由原路径处理。
- 增强 opt-in 原生用例，检查两个重复段落的后台追加、选中词精确替换，以及新 Kimi 客户端刷新标记对应的 Alt+Y 行。补了完整、多 step、缺字段、缺用量和原汇总优先的定向断言。

## Review Triage Log

### 2026-11-22 — Review pass

- verdicts: 1 finding — high 0, medium 1, low 0, false 0, maybe-false 0
- findings:
  - `medium` `patch` 隔离 Edge 启动器交接后，焦点捕获超时可能留下测试进程。改为结束时以精确的临时浏览器配置参数查找并清理相关进程树，无需先捕获窗口身份。
- 两个无上下文 runner 和一个继承工具会话的 runner 都无法启动 Windows code-mode host。后者读取先前工具输出的完整统一差异并提出上述清理问题，导演核对了实际文件与清理路径。独立 reviewer 的文件工具检查受此限制。

## Auto Run Result

- Windows 外部语音写入改用支持 `IUIAutomation2` 的 COM 类并处理新窗口缺少关闭标志的情况。记事本和 Edge 两个 opt-in 案例均通过，验证同样的两段后台追加、前台焦点和选区保持、右 Ctrl 精确替换及回读成功反馈。
- Kimi 2.5.3 的非空终态回合从完整 step 恢复累计。真实隔离进程冷重启复测通过，Alt+Y 对应新客户端回写显示累计 110 和两个 20.0%，模型只收到原初一轮请求。四个单测覆盖已知值、缺字段、无 step 用量和原汇总优先。
- `windows_voice_target.py` 和 `mc_frame_voice.py` 修复 Windows 写入。`kimi_server_v2.py` 恢复终态用量。定向单测、两个 opt-in 文件、测试摘要和本计划记录了结果与复测入口。
- Quick 修复 1 项 medium，defer 与拒绝均 0 项。未达到建议再次审查的阈值。
- 受影响核心用例 307 通过，另有 1 项热键假对象失败在精确基线复现，最终定向命令排除此同一用例。`git diff --check` 通过。明细见 [测试摘要](tests/test-summary.md)。
- Windows 使用合成识别和测试 Ctrl 释放回调。物理键盘、音频、云 ASR、Kimi 云配额仍需对应现场验收。Android 测试与日常应用不在本轮修复范围。
