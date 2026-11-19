---
title: '按职责拆分 MC 入口、存储、Kimi 客户端与测试'
type: 'refactor'
ticket: ''
created: '2026-11-19'
status: 'done'
baseline_revision: '4ea91777336e650603ea1e80e589f080566c9b72'
route: 'full'
route_source: 'auto'
review: 'thorough'
review_source: 'auto'
lenses_ran: [blind-hunter, edge-case-hunter, verification-gap, intent-alignment]
review_loop_iteration: 0
followup_review_recommended: false
context: ['{project-root}/AGENTS.md', '{project-root}/docs/handoff.md', '{project-root}/docs/experience.md', '{project-root}/docs/reflection.md']
warnings: [multiple-goals, oversized]
deferred:
  - summary: >-
      实体键盘、读屏和独立云端仍待现场验证。
    evidence: |-
      源码测试覆盖 wx 流程，冻结包使用私有 Windows 桌面和回环模型服务。
    severity: low
---

<intent-contract>

## Intent

**Problem**　桌面入口 `main.py` 约 24,850 行，`ChatFrame` 集中界面、聊天状态、远程和模型运行时。相关测试、聊天存储和 Kimi 客户端也需要明确的查找边界。

**Approach**　完成整轮职责拆分，覆盖 `main.py`、`test_main_unit.py`、`test_kimi_integration.py`、`chat_store.py` 和 `kimi_server_client.py`。按功能迁移并作对照验证，完成后独立审查和隔离打包验证。

## Boundaries & Constraints

**Always**　保持用户功能、入口、wx 控件和回调顺序、线程/事件归属、SQLite 连接/事务、Kimi 锁和代次、现有测试注入点。沿用现有消息格式、临时测试路径和注入。迁移代码按职责分组，校验原实现内容和测试身份。wx 测试串行。

**Never**　日常安装目录、现用凭据与数据、应用实例、旧存储实现的去留和功能开发均独立于本轮。交付模块用显式、静态的类和导入，避免运行时 `exec`、反射挂载或生成的包装器。不得为通过测试删除断言或跳过用例。

</intent-contract>

## Code Map

- `main.py` 保留实际入口、`ChatFrame` 和 `ChatApp` 的生命周期、已公开的 `main` 符号与源/冻结路径。`ChatFrame` 966 个方法，构造、事件注册和退出留给主类。多处测试和脚本替换 `main` 依赖，并有直接调用类方法且 `self` 为 `SimpleNamespace` 的用法。
- 用扁平职责 mixin 或纯代码模块机械迁移。普通依赖静态导入；需要动态读取兼容锚点的模块，经集中且显式初始化的 `mc_frame_bindings.app` 或等效窄入口读取 `sys.modules[__name__]`，兼容源码执行、冻结 `__main__` 和 `import main`。提前处理运行时默认参数、注解、嵌套函数和 `__file__`。现有类级访问和方法互相调用保留。`super()` 的 wx 构造继续留在原类。
- `chat_store.py` 保留 `ChatStore` 及现行连接、初始化、事务入口。独立 schema/迁移、通知发布、执行持久化与投影模块。核对 monkeypatch 时钟和执行可见性切入点。
- `kimi_server_client.py` 保留 `KimiServerClient` 和公开导出；先移 `KimiEvent`、纯协议映射，再按职责移动 V2 与 WS 实现。锁、队列、启动/关闭、代次和响应身份跟随同一个客户端对象。
- `tests/test_main_unit.py` 约 880 个函数，`tests/test_kimi_integration.py` 约 141 个函数及共享回环控制服务 fixture。按相关领域拆，保持 fixture、参数化和断言。三处按源码文本检查 `main.py` 的用例迁移到相同生产表面，避免断言空跑。

## Tasks & Acceptance

**Execution**

- [x] 在 `4ea91777336e650603ea1e80e589f080566c9b72` 记录 pytest 用例列表、代表测试与既有失败。对冻结基线先用临时路径构建 `package_mc.ps1 -BuildOnly -DistPath <temp> -WorkPath <temp>`，复用现有构建/验收方式。真实云端缺少独立认证时如实记录，以本地协议与冻结入口实测继续独立任务。
- [x] `main.py` 迁移独立呈现、对话框、常用命令、聊天信息、文件、笔记、历史、回答、F1、远程、语音热键和 provider 会话。主文件保留装配、绑定与入口。
- [x] `chat_store.py` 按 schema、通知/发布、执行职责拆，校验操作仍走原连接、原事务。`kimi_server_client.py` 拆事件/转换、V2 回放与 WS 职责，保持被测试的客户端 API 和外部导入。
- [x] 将两份巨型测试按职责迁移，显式导入合用辅助方法与共享 fixture。对比迁移前后用例名与参数实例，保留 mark，记录有意调整的源码结构断言。
- [x] 完成 Python 源码与 PyInstaller 隔离验收，记录两份生产可执行文件、已验证功能与不能实际覆盖的场景。生产或测试模块每个完整批次可独立检查，源/测试差异可追溯。

**Acceptance Criteria**

- Given 用户使用当前快捷键、菜单与模型入口，when 从源码或隔离包启动、使用回答/执行页面、切换聊天并退出，then 焦点、内容、数据路径、模型和回调归属与基线一致。
- Given SQLite 旧数据、并发/重放消息和已连接的远端，when 写入、迁移、分页、取消、恢复或关闭，then 事务、协议响应和代次仍按现有和测试约定工作。
- Given 基线测试用例已收集，when 对比拆分后相关用例和非实时回归，then 用例及参数实例完整，所有新增失败均查清并由拆分补丁修正。
- Given 源码与打包验收均完成，when 检查拆分后的内容，then 主文件职责收敛，原巨型测试、存储和客户端按清晰职责追踪，并区分迁移行数和实际删行。

## Implementation Notes

- `ChatFrame` 保留 966 个原有方法，其中 937 个迁入 16 个职责类。`ChatStore` 保持 97 个原有方法，schema 保持原连接，移出的运行时取同一时钟。`KimiServerClient` 保留 61 个原有方法。AST 对照确认主体相同，差异包括明确的原模块依赖绑定、时钟访问和 schema 提取。
- 两份大测试的 880 和 141 个测试函数均保留。3,036 个非实时实例逐个对照，1,220 个实例迁移文件。三个测试方法的结构断言由原路径或原类名调整到当前实现，另有打包检查验证重构模块的实际依赖。最终修复另加 5 个入口实例，共收集 3,041 项。
- 拆分前后定向对照 2,078 个原用例实例，1,941 项通过，136 项失败在相同节点复现，1 项在两版都遇到相同 Windows COM 阻塞。全量单进程基线出现 Windows 原生故障，定向对照按文件串行运行。全量非实时验证未完成。
- 基线与候选包均从独立目录用正式打包脚本 `-BuildOnly` 成功构建。基线 SQLite 夹具、真实 2.5.3 和回环模型完成两问，确认同 chat/session、不同 prompt、原生列表和 SQLite 可见、REST 与正文一致、退出重启恢复。两版 `mc_worker.exe` 响应 ping 和 `--browser-open --help`，源码 `main.py --codex-worker` 响应 ping。冻结验证使用全套临时数据，测试子进程和临时数据已清理。
- engineer 独立复核原方法体、逐用例收集、基线失败分布和两份冻结包证据。2,078 个实例属于定向验证范围，其余 958 个在这轮定向运行范围之外。
- 内置审查修复后 34 个针对性用例通过，包括原先被 COM 对话框阻塞的忙碌发送断言。该阶段默认和 importlib 两种收集模式均保留全部 3,036 项。独立复审修复后的通知 17 项和入口 5 项均通过。
- 早期直接构造 frame 的回归可能初始化 `D:/code/note/notes.db`。该文件的修改时间落在首轮测试期间，只读 `quick_check` 通过，具体内容变化无法确认。修订后 autouse 夹具在每个用例中设置临时笔记路径，之后定向复跑前后源码笔记库与 SQLite 辅助文件的存在、写入时间和大小一致。

### 物理行数

| 原文件 | 拆分前 | 入口现状 | 新职责文件总行数 | 模块组净变化 |
|---|---:|---:|---:|---:|
| `main.py` | 24,850 | 1,494 | 23,982 | +626 |
| `chat_store.py` | 2,731 | 919 | 1,859 | +47 |
| `kimi_server_client.py` | 2,348 | 690 | 1,723 | +65 |

这些是截至 `947056c` 的物理行数。入口减量来自迁移，拆分后的 Python 文件增加了各自的导入与模块声明。三个生产模块组最终净增 738 行。

## Plan Change Log

- 2026-11-20 收尾更新了交接中的语音测试入口，并标明拆分前测试结果适用的版本。

## Review Triage Log

### 2026-11-19 — Review pass

- verdicts: 17 findings — high 2, medium 3, low 12, false 0, maybe-false 0
- findings:
  - `[medium]` `[patch]` 次级 runpy 执行覆盖已运行入口绑定，且自定义名字未注册时会抛出 KeyError。限制为真实入口绑定，并验证导入场景。
  - `[medium]` `[patch]` 独立导入后的纯文本 helper 调用没有入口 binding 时抛出 AttributeError。提供本地默认实现，保留有效入口替身。
  - `[medium]` `[patch]` 两份原测试在 importlib 模式可收集，其迁移文件缺少 tests 支持目录。补齐已有测试路径设置并核对两种收集结果。
  - `[low]` `[patch]` 原 Kimi 无 wx 导入约束遗漏新模块。将同一约束扩展到迁移后的所有客户端模块。
  - `[low]` `[patch]` 打包验收新增了对调用表达式拼写的检查。改用真实 frame 的客户端构造入口验证类型和依赖。
  - `[low]` `[patch]` 已迁移的 NATS 启动、远程取消与已读用例的文件归属误导定向选择。移动到对应的功能测试文件。
  - `[low]` `[patch]` NATS 调度用例仅检查通过名称识别的 worker。继续执行捕获的 worker，核对启动回调次数。
  - `[low]` `[defer]` 文档的旧语音验证命令仍指向原测试文件。由 neat-freak 收尾修订新入口并标注历史证据。
  - `[low]` `[reject]` 验证记录未列出全部未运行节点。报告已限定定向范围，已保存对照失败和崩溃证据，避免将收集数量视为运行结果。
  - `[low]` `[defer]` 实体键盘、读屏反馈等仍缺现场验证。隔离包验证结果与实体交互独立记录。
  - `[low]` `[reject]` 计划应区分迁移与减量。实际数字加入收尾记录，该请求属于结果整理。
  - `[high]` `[patch]` 直接创建 frame 的旧库迁移测试绕过笔记路径夹具。统一为每个测试建立临时路径，独立调用解析器的用例显式使用原函数。
  - `[low]` `[defer]` 边界审查重复发现交接文档的旧语音命令。并入相同的文档修订。
  - `[low]` `[patch]` 忙碌时普通模型发送的既有测试在两版都触发原生错误窗口，未到达实际断言。更新测试替身并验证有第二个回复。
  - `[low]` `[patch]` 执行页过期测试通过显式 now 跳过了新时钟读取。补齐默认时钟路径的过期检查。
  - `[low]` `[patch]` 内容分页回归只有按 chat revision 拒绝游标的覆盖。补齐基于实际时钟的游标过期检查。
  - `[high]` `[patch]` 验证镜头再次确认直接创建 frame 会用真实笔记路径。并入共享测试隔离补丁。源码笔记库修改时间落在测试期间，只读完整性检查通过，具体内容变化无法核实。

## Verification

- `.venv/Scripts/python.exe -m pytest --collect-only -q -p no:cacheprovider -m 'not live'`，逐个比较参数化 nodeid；可临时保留旧至新映射以核对完整性。
- `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider -m 'not live'` 或在相同环境按文件分组 serial 运行，明确原有失败和未覆盖条件。含回答、常用命令、笔记、语音、历史、Codex/Kimi、远程、聊天存储与打包测试。先建立定向基线并分批复测直至整轮范围可对比。
- 对正常入口、常量解析、迁移方法和测试做 AST/符号对照、编译、`git diff --check`。用 `package_mc.ps1 -BuildOnly` 指向独立目录验证实际 frozen 产物，并使用临时笔记和历史、回环或可用独立认证验证关键调用链；保留实际结果的边界。

## Code Review

### 2026-11-20 · 独立复审

**Code review complete.** 0 decision-needed, 2 patch, 0 defer, 0 rejected.

- [x] 独立 dog 运行 `bmad-code-review` quick。修复 VS Code 调试入口和 CPython 紧凑 `-mmain` 的静默退出。
- [x] 将通知游标 TTL 断言移至实际创建续页游标并替换时钟的测试。
- 两项修复均由 `947056c8d329713c731a3211495bec27358c000b` 完成。通知 17 项、入口 5 项通过，内置 quick 复审 0 发现。真实 VS Code debugpy worker 与修复后的隔离包复验通过。复现命令与证据见 [独立复审修复计划](plan-mc-code-split-review-fixes.md)。

Rejected: 无。

## Auto Run Result

- 完成桌面入口、聊天存储、Kimi 客户端及两份大型测试的拆分。实现和物理行数见 Implementation Notes。
- 内置审查合并重复项后修补 1 个 high、3 个 medium 和 7 个 low 条目，独立 quick 复审另确认并修复两项问题，详见 Code Review。交接已更新语音测试入口。
- 正式 `-BuildOnly` 隔离构建后的基线与最终版通过 `mc.exe` 和 `mc_worker.exe` 验收。真实 Kimi 2.5.3 经回环服务完成两问，核对同 chat/session、不同 prompt、原生界面与 SQLite/REST 一致以及重启恢复。真实云端、物理键盘和实体读屏仍待验证。
