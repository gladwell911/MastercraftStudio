---
title: '修复拆分复审发现的入口和通知测试问题'
type: 'bugfix'
ticket: ''
created: '2026-11-19'
status: 'done'
baseline_revision: '85455d19738aa2338f22244d075fe9d75f48ab48'
route: 'full'
route_source: 'pinned'
review: 'quick'
review_source: 'pinned'
lenses_ran: [quick]
review_loop_iteration: 0
followup_review_recommended: false
context: ['{project-root}/AGENTS.md', '{project-root}/docs/handoff.md']
warnings: [multiple-goals]
deferred: []
---

<intent-contract>

## Intent

**Problem**　新 dog 独立复审确认，VS Code 的 `Run Main` 和 CPython 紧凑 `-mmain` 静默退出，通知清除测试又因错放的游标过期断言而失败。

**Approach**　恢复原支持的显式启动命令，并将错误位置的测试断言移到持有游标及替身时钟的续页测试中。

## Boundaries & Constraints

**Always**　真实入口中的首个注册模块持有应用绑定。普通导入和已运行入口的二次加载保留原绑定。沿用既有每用例临时笔记库。测试使用本地 worker 输入和临时 SQLite。

**Never**　本补丁的范围限于两项独立审查发现，日常程序部署和笔记数据操作在本轮之外。

</intent-contract>

## Code Map

- `mc_frame_bindings.py` 的 `bind_entry` 已检查首次绑定及 `sys.modules` 中模块字典与传入命名空间的同一性。移除 `__main__` 分支对 loader、spec 和 argv 拼写的附加限制。保留真实 `import main` 的初始化判断、首次绑定限制，以及 `main.py` 的启动判断。`runpy` 显式注册的 `__main__` 是 `.vscode/launch.json` 所用入口。
- `tests/test_main_entry.py` 使用现有 pytest 和 `subprocess`。追加定向入口验证的参数化实例。输入一条本地 JSON ping，验证退出码、ready 和关联 pong 的 ID。测试不构造生产窗口。
- `tests/test_notification_sync.py` 的 `test_clear_and_delete_authority_prevent_old_final_resurrection` 没有 `cursor` 和 `monkeypatch`。将过期判断移到 `test_long_content_utf8_continuation_rejects_wrong_owner_and_clear` 创建第二个游标之后、执行 clear 之前。替身上下文结束后复用现有 clear 验证。

## Tasks & Acceptance

**Execution**

- [x] `mc_frame_bindings.py` 修复显式 `__main__` 绑定。
- [x] `tests/test_main_entry.py` 覆盖直接运行、分隔 `-m`、紧凑 `-m`、调试器形态的 `runpy.run_path(..., run_name='__main__')` worker pong，并保留已加载 `main` 的次级命名空间不能替换绑定的探针。
- [x] `tests/test_notification_sync.py` 修复断言位置，运行完整通知测试模块。

**Acceptance Criteria**

- Given 独立进程运行既有启动命令或调试器路径，when 通过 `--codex-worker` 提交 ping，then worker 输出 ready 和相同 ID 的 pong 并正常退出。
- Given 一个已绑定的应用，when 加载次级运行命名空间，then 原应用绑定保持同一对象。
- Given 通知游标从同一 chat revision 产生，when 默认读取时钟超出 TTL，then 续页返回原有过期错误，并且原有 clear 和删除行为检查运行通过。

## Implementation Notes

- 2026-11-20 验证。通知模块 17 通过，入口定向 5 通过，合计 22 项。基线迁移的 3,036 项加 5 项入口回归，最终收集 3,041 个非实时实例。定向复跑前后源码笔记库元数据一致。
- engineer 用本机安装的 VS Code Python Debugger、仓库中配置的程序、解释器与工作目录实测 worker 模式，收到关联 pong。正常源码子进程及模拟 debugger 也通过。
- 重新从临时路径运行 `package_mc.ps1 -BuildOnly`，冻结 worker ping/pong、实际 Kimi 2.5.3 的两轮回环、界面与 SQLite、REST 核对、重启复载均通过。验收使用临时历史和笔记库，测试进程与临时数据已清理。

## Plan Change Log

## Review Triage Log

### 2026-11-20 — Review pass

- verdicts: 0 findings — high 0, medium 0, low 0, false 0, maybe-false 0
- 快速复审确认两项修复满足验收条件。两个既有入口单元用例的失败节点已在拆分前后对照复现，记录在整轮计划中。

## Verification

- `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_notification_sync.py`
- `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_main_entry.py -k 'worker_entry or secondary_entry'`
- 主会话再次用独立目录重建冻结包并验证 worker 探针与 Kimi 本地双回合和重启；执行 `git diff --check`。

## Auto Run Result

- `mc_frame_bindings.py` 接受已注册的实际运行入口，保留首次绑定与命名空间身份检查。`tests/test_main_entry.py` 增加 5 个针对性入口实例。
- `tests/test_notification_sync.py` 恢复原清除与删除断言，并在真实的续页游标上检查过期。
- 通知 17 项、入口 5 项、真实 debugpy worker、隔离冻结包双回合和重启均通过。快速复审补丁、延期、驳回各 0 项。实体输入、读屏及真实云端不在本次诊断的验证范围内。
