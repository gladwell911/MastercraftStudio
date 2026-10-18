---
title: '可携带的网页打开与前台显示'
type: 'feature'
ticket: ''
created: '2026-10-18'
status: 'built'
baseline_revision: 'de51a45050170f57d4bd6e63b6bdc9ccca2e1e76'
route: 'full'
route_source: 'auto'
review: 'thorough'
review_source: 'auto'
lenses_ran: ['blind-hunter', 'edge-case-hunter', 'verification-gap', 'intent-alignment']
review_loop_iteration: 0
followup_review_recommended: false
context:
  - 'D:/code/sj/mc/AGENTS.md'
  - 'D:/code/sj/mc/docs/experience.md'
  - 'D:/code/sj/mc/docs/reflection.md'
  - 'D:/code/sj/_bmad-output/implementation-artifacts/plan-portable-browser-open.md'
warnings: ['oversized']
deferred: []
---

<intent-contract>

## Intent

**Problem:** MC 已支持自动审查，但默认仍为 never，缺少可随源码和安装包携带的浏览器入口。另一台已安装 Chrome、Codex 的 Windows 电脑需要拉取并打包后直接用自然语言打开 HTTP(S) 网页并显示到前台。

**Approach:** 按既有实施计划提供单模块浏览器 helper、随包 mc-browser-open 技能、运行态 argv 与技能发现链。统一自动审查默认及一次旧配置迁移，沿用现有 worker 和 onedir 打包。

## Boundaries & Constraints

**Always:** 默认原生三元组为 on-request / workspace-write / auto_review；迁移后保留用户明确选择。只操作自己创建的 target，保留全部既有 Chrome 标签。固定专用 ChromeAutomationProfile 和 127.0.0.1:9222，动态核对根进程、StartTime、profile、监听归属与 HWND。正式审核拒绝即停止。仅隔离数据、实例和独立 DistPath -BuildOnly 验证。

**Never:** 不退出、重启或覆盖日常 MC，不写生产库或凭据，不杀共享 Chrome、不删 profile 锁、不换端口绕过冲突。不得依赖仓库外脚本、全局 web-access、固定 PID/盘符或外部 Python；不得自动降级审批或代答 accept。不增加登录/表单操作、插件平台、额外依赖或无关重构；不伪称另一台机器验收完成。

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| 默认迁移 | 新/空配置、无版本旧 never/on-request、未知模式 | auto-review；完整加载后保存 version=1 | 不提前覆盖尚未恢复状态 |
| 后续选择 | version>=1 且合法模式 | 保留模式和不低于现有的版本 | 错误类型或未知模式回退 auto-review |
| 原生 API | start/resume 省略三项；显式 never/on-request | 省略采用新默认；显式手动仍 user/danger-full-access | 不向原生发送 auto-review |
| 打开网页 | 合法 HTTP(S)，无实例或匹配可见实例 | 新普通窗口、自有 target、实际 URL/title/readyState 与前台 JSON | 超时/网络/焦点分别报告 |
| 冲突 | 非 HTTP(S)、端口占用、不可核实 profile、headless、多根 | 停止且保留现有实例 | 清晰原因，不自动改可见模式 |
| 创建后失败 | 已有自有 targetId，窗口归属不唯一或前台失败 | 立即输出并 flush targetId；只续验该目标 | 禁止盲目再次创建或猜 HWND |

</intent-contract>

## Code Map

- `codex_client.py`：顶部审批常量与 normalize/codex_approval_parameters；prepare_task:636 当前仅注册用户根，start_thread/resume_thread:652/673；_build_launch_env:893 有新旧私有 home 分支。
- `main.py`：审批初始化:1992、_load_state:4430（changed 在审批字段之后初始化）、_save_state:4582、后续请求与审批菜单；统一 fallback，不改变活动 steer。
- `codex_worker_process.py` / `codex_worker_client.py`：三处入口使用共享审批 helper，client 透传 kwargs；保留现有归属与协议。
- `worker_launcher.py`：当前顶层导入 worker main，须在 stdin 协议前分派 --browser-open；`zgwd.spec` / `package_mc.ps1`：默认 onedir 双 exe、Assert-PackageArtifacts。
- `tests/test_codex_client_unit.py`、`tests/test_codex_command_approval_unit.py`、`tests/test_codex_command_approval_ui_automation.py`、`tests/test_codex_worker_process.py`、`tests/test_packaging_specs.py`：复用现有定向夹具；wx 串行，纯 client/worker/packaging 可 --noconftest -o pythonpath=.。

## Tasks & Acceptance

**Execution:**
- [ ] `codex_client.py`, `main.py`, `codex_worker_process.py` -- 统一 mode/native 默认解析和 codex_approval_defaults_version 最小一次迁移 -- 后续新建/恢复自动审查，手选持久保留。
- [ ] `browser_open.py` -- 动态源码/frozen 资源与 argv；Chrome 探测、metadata 核验、CDP websocket、创建/续验自有 target、Win32 前台及 JSON CLI -- 提炼既有成熟代码，去除历史常量；导入无副作用。
- [ ] `assets/codex-skills/mc-browser-open/SKILL.md`, `codex_client.py` -- 合并去重用户与随包 extraRoots，forceReload，双环境分支注入 MC_BROWSER_OPEN_ARGV JSON -- 透明 PowerShell 数组调用，不用 Invoke-Expression；普通搜索不启 Chrome，真实 deny 停止。
- [ ] `worker_launcher.py`, `zgwd.spec`, `package_mc.ps1` -- 先分派 helper，静态收集模块/既有 websocket 与技能，检查非空 SKILL -- 无 helper 参数时 worker 行为不变，补假构建器夹具。
- [ ] `tests/test_browser_open_unit.py` 及上述定向测试 -- 覆盖矩阵、源码/frozen/空格路径、发现合并、worker 无 ready、副作用和打包 -- 检查实际 JSON-RPC 参数。
- [ ] `AGENTS.md`, `docs/handoff.md`, `docs/experience.md` -- 简短更新入口、默认和真实验证边界 -- 避免保留过时默认说明。

**Acceptance Criteria:**
- Given 新配置或旧配置首次升级，When 通过 MC 创建/恢复 Codex 任务，Then 发送新的原生三元组；Given 迁移后手选模式并重启隔离 MC，When 再创建任务，Then 使用该模式；活动 steer 不重启。
- Given 含空格的源码搬移目录及独立安装包，When 原生 skills/list 和 MC 自然语言请求打开网页，Then 能发现随包技能、模型实际读取并调用正确 helper argv，用户技能仍并存；安装包不依赖源码或系统 Python。
- Given 已授权的可见网页动作，When 专用 Chrome 首启或复用，Then 页面实际加载且自有 HWND 可见、非最小化并等于 GetForegroundWindow，原标签和日常 Chrome 保留；审核若触发记录真实结果，未触发如实记录。
- Given 另一台电脑尚未执行验收，When 本机验证完成交付，Then 明确保留该机首启/焦点验证待办，不以本机结果代替。

## Implementation Notes

已按既有方案完成默认迁移、动态 helper、随包技能与 worker/打包接入。初次真实执行发现 PowerShell 前导字符、5.1 JSON 数组接收及 GBK 输出问题，均局部修复；首次源码前台失败如实保留，打包同 target 续验已取得真实前台成功。engineer 独立核对原始命令结果，未代答审批。

审查补丁仅涉及 helper、技能与相关测试：激活前再次确认 CDP windowId 与 HWND；明确瞬态错误在截止时间内续试；连续事件不能突破调用总超时；首次记录写入失败标明不可续验；提前错误保留 targetId。实例 PID/StartTime 变化、监听/窗口延迟及上述错误路径均有定向测试。实际复验发现严格要求整个差集唯一会误拒绝持久化的自有窗口，已局部纠正：初次无已确认 HWND 仍要求唯一；续验核实原 HWND 仍存在且 CDP windowId 不变，允许无关窗口共存。director 与 engineer 最终 helper 各 46 项通过。没有重推方案或执行额外审查轮次。

## Plan Change Log

## Review Triage Log

### 2026-10-18 首轮分诊

17 条逐项分诊：high 2、medium 8、low 1、false 6、maybe-false 0；重复发现合并修复，不增加评审轮次。均为局部 patch 或 reject，无 bad_plan；intent-contract 保持不变。依据当前 browser_open.py、27 项 helper 测试、engineer 报告及最终 package-engineer.json 的真实 commandExecution（status=ok、同 HWND 前台、exitCode=0）；旧报告中的“进行中”由最终 JSON 和当前交接正文补足。

| ID | Verdict | Route | 证据与处理 |
| --- | --- | --- | --- |
| B1 | high | patch | open_page 取过 windowId，但激活前不复核；目标随后移到旧窗口且另开一个窗口时，唯一 HWND 差集可指向别页。激活前再次 Browser.getWindowForTarget 确认 windowId 未变，并重新核对唯一 HWND 差集；变化或不唯一即停止。加入该具体错配测试，不新增标识注入或窗口管理架构。 |
| B2 | medium | patch | create 后立即 getWindowForTarget，无窗口尚未就绪的有界重试。仅对明确瞬态窗口未找到错误在现有 deadline 内等待，不重发 create，不吞其他 CDP 错误。 |
| B3 | false | reject | 单次 foreground=false 是正确失败而非伪成功；最终真实打包续验同 target 返回 foreground=true、HWND=foregroundHwnd、exit0，不能再以缺少核心成功证据要求改焦点策略。 |
| B4 | medium | patch | Runtime.evaluate 的 context destroyed/cannot find context 会直接退出，正常导航期间可发生。只识别明确 execution-context 瞬态错误并在已有 deadline 内继续，其他协议/网络错误保留。 |
| B5 | medium | patch | CDP.call while True 仅依赖每次 recv 的 socket timeout；连续事件可无限延长。加单次调用总 deadline，recv 剩余超时与循环检查共同有界；测试持续非匹配事件。 |
| B6 | medium | patch | 首次 save_receipt 失败后已 flush targetId，但没有可读 receipt，技能仍提示续验。最小修复为明确返回 continuationAvailable=false 及保留 target 不重建，技能按该结果停止并报告；不引入无记录的任意 target 接管。 |
| B7 | false | reject | helper 从不关闭既有 target；用户自行关页也会使 preexistingTargetsPreserved=false。这是观察字段，不足以推断 helper 破坏或否定自己页面成功；不增加全局标签保活契约。 |
| B8 | low | patch | ensure_instance 的无 listener→有 listener、延迟 HWND 缺定向时序测试；补最小顺序快照与延迟窗口用例，错误归属仍立即失败，不重复真实全套验收。 |
| B9 | false | reject | 当前 docs/handoff.md 已写真实构建及同target前台成功和跨机待办；旧 diff 的“进行中”不再是当前正文缺陷。 |
| E1 | medium | patch | 同 B5；合并 CDP.call 总 deadline 修复与持续事件测试。 |
| E2 | high | patch | 同 B1；合并具体“target 移旧窗口+另一新窗口唯一”的错配测试及保守停止，不另开实现。 |
| E3 | medium | patch | 同 B6；合并 continuationAvailable=false 与技能停止指引，不增加任意目标恢复机制。 |
| E4 | medium | patch | target-id 分支的 process_snapshot/guard/connect 在 try 外抛出，main 最终 JSON 丢失调用者已知 targetId。main 异常结果保留 args.target_id，并补 metadata 失败 CLI 测试；不创建新窗口。 |
| G1 | medium | patch | 当前 create/continue 测试将 guard mock 为空，未证明 PID/StartTime 跨快照变化被阻止。保留真实 guard/inspect_instance，只 mock 底层快照和命令行解析；分别覆盖 PID 与 StartTime 变化及创建/激活之前停止。 |
| I1 | false | reject | diff 本身不包含工作流运行日志属证据范围差异，本会话已实际运行 renderer 并依序执行 snapshot；无需产品修改。 |
| I2 | false | reject | mock 不能证明真实结果的原则正确，但本轮已有真实源码发现、模型读技能、自动审核 approved、真实 onedir 和打包同target前台成功 JSON；缺少真实验收的断言已过时。 |
| I3 | false | reject | 计划和最新交接明确另一台电脑首启/焦点待办，从未称通过；不为不可访问目标机伪造结果或阻塞本机局部代码交付。 |

## Verification

**Commands:**
- `.venv/Scripts/python.exe -m pytest tests/test_browser_open_unit.py tests/test_codex_client_unit.py tests/test_codex_worker_process.py tests/test_packaging_specs.py --noconftest -o pythonpath=. -q` -- 核心 helper、原生请求和打包契约通过。
- `.venv/Scripts/python.exe -m pytest tests/test_codex_command_approval_unit.py tests/test_codex_command_approval_ui_automation.py -q` -- 现有 wx 夹具串行验证状态、菜单及 IPC。
- `powershell -NoProfile -File package_mc.ps1 -DistPath <独立临时父目录> -WorkPath <独立构建目录> -BuildOnly` -- 实际独立构建、技能非空，随后运行产物 mc_worker.exe --browser-open --help。
- 源码搬目录、独立包原生技能发现及自然语言调用实际浏览器 -- 记录有效参数、target 和 Win32 状态；仅必要隔离宿主，不接管生产配置。
- `git diff --check` -- 无空白错误；engineer 独立复核影响范围证据。

## Auto Run Result

- 已实现：自动审查默认与一次迁移、源码/frozen 浏览器入口、随包技能发现、运行态 argv、worker 提前分派和 onedir 打包收录/检查。
- 文件：`codex_client.py`、`main.py` 负责默认与发现；`browser_open.py` 负责自有页面和前台检查；`assets/codex-skills/mc-browser-open/SKILL.md` 负责模型触发；`worker_launcher.py`、`zgwd.spec`、`package_mc.ps1` 负责打包执行；相关测试及 AGENTS/交接/经验已更新。
- 审查：17 条逐项记录如上，11 条有效发现合并为 8 组局部补丁（high 1、medium 6、low 1）；6 条 reject 均有反证；无 defer。未增加第二轮审查。B1/E2 的续验检查经实际新增无关窗口场景复验后纠正过严条件，保持原 windowId 与已确认 HWND 核对。
- 验证：原核心 163 项通过后，唯一旧默认断言精确复验通过；审批迁移/菜单/IPC 50 项通过；最终 helper director 和 engineer 分别 46 项通过。最终非管理员隔离构建 exit 0、打包 worker help exit 0；最终产物对同一自有 target 续验 exit 0、status=ok、complete、visible=true、minimized=false、foreground=true、原 targets 保留。源码换目录原生发现及模型读技能/调用、打包模型读随包技能与用户技能并存、真实官方自动审查 approved 均已独立核对。无变化检查复用对应版本证据。
- 证据：完整独立报告和私有日志位于仓库外 `../../../_bmad-output/implementation-artifacts/verify-portable-browser-open-engineer.md`。中间源码前台失败、过严差集失败和测试宿主完成检测问题分别保留，没有拼接成同轮全绿。
- 剩余边界：没有关闭共享 Chrome 制造无实例/profile 首启；另一台电脑首启、sandbox 前置状态、实际焦点及完整正式 MC GUI 尚待验收。本轮未退出/重启/覆盖日常 MC、未写生产数据或凭据。
- followup_review_recommended: false。8 组补丁已由针对性回归及 engineer 最终产物复验覆盖，没有需要额外审查才能核实的具体补丁风险；上述目标机/首启边界单独保留，不扩大审查。
- 交付方式：本地提交，按本技能 Finalize 的明确要求不推送。
