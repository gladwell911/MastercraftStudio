---
title: '全局技能目录复用与打包成功后启动'
type: 'feature'
ticket: ''
created: '2026-10-06'
status: 'built'
baseline_revision: '171c3bc1831b45ad545fdd802ee3404237889412'
route: 'full'
route_source: 'auto'
review: 'quick'
review_source: 'auto'
lenses_ran: ['quick']
review_loop_iteration: 0
context: ['D:/code/sj/mc/AGENTS.md']
---

<frozen-after-approval reason="用户本轮明确同意实施；变更意图须由用户重新协商">

## Intent

**Problem:** MC 隔离 Codex home 只补充缺失技能，旧目录和复制退路阻断全局更新；打包成功没有自动启动。

**Approach:** 当前用户全局技能通过透明目录链接和 Codex 原生发现复用，新任务刷新清单；成功构建、校验和更新产物后启动最终目录的程序一次。

## Boundaries & Constraints

**Always:** 保留隔离 home 的会话数据；迁移保留独有本地内容。MC 链接管理不写全局技能、不遍历删除外链目标；使用普通用户可用的 Junction 或 symlink。维持既有 provider、任务归属与 UI 线程边界。

**Never:** 本次真实打包、替换或启动 `D:/code/cx/mc`；真实模型/GUI/个人数据测试；`.lnk` 或静默退回长期副本；插件技能展开为失去插件身份的普通技能；修改 RC 或推送提交。

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| 链接复用 | 全局目录存在；已有目录副本、空目录或错误链接 | whole-directory 链接指向当前用户来源；旧普通内容移到同 home 的唯一 legacy 路径 | 不覆盖 legacy、不删除外链目标 |
| 全局更新 | 新增、修改、删除技能；同名、多来源技能 | 后续任务读最新目录；保留原生优先级和插件身份 | 不修改全局文件（包括 BOM） |
| 源缺失/链接失败 | 某来源不存在或权限不足 | 缺失来源可跳过并保留旧内容；已存在来源无法链接明确报错 | 不伪装为更新成功；可重试 |
| 打包失败 | 构建非零、缺产物、更新或启动失败 | 不启动旧包/临时包；失败返回非零 | 构建校验前不清理旧包 |

</frozen-after-approval>

## Code Map

- `codex_client.py` — home 初始化、旧副本迁移、BOM 写入、线程启动/恢复及 turn RPC。
- `codex_worker_process.py` — 每次任务先 start/resume，再 start_turn_items；执行中追加走 steer。
- `codex_worker_client.py` — worker cwd 为模块目录，包版通常 `_internal`。
- `package_mc.ps1`, `zgwd.spec` — 当前直接先删包、PyInstaller 后清历史；已静态导入 Codex 客户端。
- `tests/test_codex_client_unit.py`, `tests/test_codex_worker_process.py`, `tests/test_packaging_specs.py` — 现有受影响验证。

## Tasks & Acceptance

**Execution:**
- [x] `codex_client.py` — 链接 whole `skills` 和必要的 `plugins` 至当前用户 `.codex` 来源；安全迁移旧目录，symlink 不可用时采用 Junction，移除沿链接改 BOM。
- [x] `codex_client.py` / `codex_worker_process.py` — 新任务准备时显式通过 `skills/extraRoots/set` 注册当前 `.agents/skills`，随后 `skills/list`（`cwds`, `forceReload:true`）；刷新先于已有 start/resume/turn 链，执行中 steer 不重新准备。
- [x] `codex_client.py` / `codex_worker_process.py` — 新任务前比较全局插件/marketplaces 配置，变化才更新对应配置并重启 app-server；保留 home，通过已有 resume 恢复原 thread，不中断执行中 steer。steer 明确报告无活动 turn 后转新任务时也先准备再恢复。
- [x] `package_mc.ps1` — 临时构建校验后更新；处理 reparse 节点时只操作链接，保留运行数据；最终 `mc.exe` 和 worker 校验通过后一次 `Start-Process`，指定最终工作目录，不等待 GUI 退出。
- [x] `tests/test_codex_client_unit.py` 与 worker 定向测试 — 隔离临时用户/home，验证矩阵及 RPC 顺序、后续任务更新。
- [x] `tests/test_packaging_specs.py` — PowerShell 隔离假构建、假启动测试，验证成功/失败分支、产物路径、外链目标与数据保留；禁止调用真实 PyInstaller。
- [x] `README.txt` / `docs/handoff.md` — 简短记录来源、legacy、失败行为和打包成功自动启动；说明本次仅源码与隔离测试。

**Acceptance Criteria:**
- Given 当前用户的两个全局技能来源，when 首次启动 Codex 或恢复聊天，then 全部可发现技能可达，来源新增和删除无需重打包。
- Given app-server 已运行且同聊天再次提交，when 全局技能更新，then turn 前刷新并读取新内容；执行中追加输入不重启当前任务。
- Given 已启用插件及新增插件配置，when 开始新任务，then 使用最新全局插件来源及身份；不只列出普通技能。
- Given 存在旧副本和外链，when 重复初始化，then 幂等链接且独有文件可恢复、全局文件字节及外链目标均不改变。
- Given 假构建成功，when 所有必要校验与更新成功，then 只启动最终产物一次；Given 任一阶段失败，then 无启动且非零退出。
- Given 打包更新既有目录，when 清理/迁移发生，then 外链目标、包外 history/OneDrive 数据及包内 Codex 会话数据保留。

## Implementation Notes

- director 已完成 8 个产品/测试/说明文件的修改；整目录链接、legacy 保全、新任务刷新、插件配置变化时重启、临时构建与最终目录一次启动均已落地。停旧进程显式唤醒 pending 请求，过期 reader 不影响新进程等待者。
- 自验三个定向文件 122 passed；最后配置序列化调整后 client 单独 64 passed。PowerShell AST、Python 编译与 diff 空白检查通过。真实原生发现与插件身份尚待 engineer 的隔离非模型协议探针，不以 schema/mock 替代。
- 更新文件阶段失败可能留下部分程序文件；会话/history 保留且不启动，重新运行脚本可完成更新。本次不增加旧包备份或发布回滚流程。
- engineer 独立以 `--noconftest -o pythonpath=.` 复跑三个文件：122 passed、0 failed、0 skipped；5 个 Python AST、PowerShell AST、staged/working diff 空白检查通过。矩阵每行均由实际运行的测试覆盖，含 Windows Junction、legacy、BOM/外链保全、缺源/失败恢复及打包七分支。
- Codex 0.160.0 非模型原生探针确认两来源及同名记录按相同 extraRoots 配置保留原生发现结果；普通/同版本插件技能增改删可在同进程刷新，插件身份与启用配置变化重启已确认。46 次非模型 RPC，无 thread/turn/model 请求。写入、配置和认证均在临时目录；原生额外只读当前用户 `.agents` 技能元数据，不能称完全隔离。未验证模型同名最终选择、跨缓存版本替换、真实 thread 恢复或安装包。
- 按用户任务规模与最少代理要求，采用一轮 president 限定范围 quick 检查，省略默认四路 thorough 评审。依据已有证据验证必要配套，不扩展到完整 GUI 或全系统回归。

## Plan Change Log

## Review Triage Log

- 单轮 president quick 检查：high 0、medium 0、low 0、false 0、maybe-false 0，无阻塞发现。已核对新任务/steer 调用链、Junction/legacy 节点保全、pending 唤醒与 reader 归属、打包校验/更新/一次启动顺序，复用 122 项独立测试及两份实际 native trace；无需修补或扩大验证。

## Design Notes

整目录链接避免逐技能同步；`.agents/skills` 与插件发现沿 Codex 自身规则，避免自建合并目录。全局插件新增可能同时更改配置；采用配置变化才在新任务前重启 app-server 的确定行为，不依赖未证实的配置热载。实际新任务必经 worker start/resume，准备和重启放在此之前；执行中 steer 仍走原客户端。

本机 Codex CLI `0.160.0` 离线生成协议确认 `skills/list.forceReload`、`skills/extraRoots/set.extraRoots`；仅 schema 不能证明运行时 `.agents` 或插件刷新。验证可使用隔离本机 CLI 的非模型协议请求；不能调用真实用户 home。包外数据 `resolve_app_data_dir()` 指向产物父目录的 `history`；包内 `.codex-home` 仍须保留。当前清理先递归重设属性，必须消除跨 reparse 的遍历。

## Verification

**Commands:**
- `.venv/Scripts/python.exe -m pytest tests/test_codex_client_unit.py tests/test_packaging_specs.py -q` — 受影响测试通过，PowerShell 假构建/启动只触及 tmp 路径。
- `.venv/Scripts/python.exe -m pytest tests/test_codex_worker_process.py -q` — 现有任务启动、恢复与追加归属保持。
- PowerShell AST 解析 `package_mc.ps1` 与 touched Python 编译、`git diff --check` — 无语法或空白错误。

engineer 独立复跑同范围并检查真实 Windows 链接（临时来源）更新/删除可见、迁移保全、全局字节不变及成功/失败启动路径；不要求完整 GUI/全系统回归。
