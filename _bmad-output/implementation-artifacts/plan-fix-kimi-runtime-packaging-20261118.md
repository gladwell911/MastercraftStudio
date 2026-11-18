---
title: '修复日常 MC 缺少固定 Kimi 运行时及打包遗漏'
type: 'bugfix'
ticket: ''
created: '2026-11-18'
status: 'built'
route: 'full'
route_source: 'pinned'
review: 'quick'
review_source: 'pinned'
lenses_ran: ['quick']
review_loop_iteration: 0
baseline_revision: '397d2dab52a062c5e00a51767d1f362507224e49'
context: ['{project-root}/docs/kimi-runtime.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

11 月 18 日日常 MC 的 Kimi 首轮在解析固定路径时失败。补齐缺少的本地运行时，并确保以后通过正式打包入口发布的 MC 包具备同一可执行文件。

## Boundaries & Constraints

只显式使用本机已核验的 2.5.3 二进制。保持哈希、版本、禁止自动更新和禁止 PATH 回退的现有约束。不得停止、启动或替换日常 MC，不向日常聊天发送探针，不写聊天、笔记或全局配置，不提交二进制。日常目录本次仅允许准备该缺失文件及必要父目录。

## I/O & Edge-Case Matrix

| 输入或状态 | 结果 |
|---|---|
| 已显式准备可信源码运行时 | 正式 spec 将原文件随包交付到固定相对路径 |
| 来源缺失、SHA256 不符或版本探针失败 | 终止并提示如何显式准备，校验哈希通过后才运行版本探针 |
| 自定义 spec 漏带、暂存包文件损坏 | 更新前失败，原输出及用户数据保留 |
| 最终副本校验失败 | 返回失败并阻止启动 |
| 日常目标已存在 | 验证并复用有效目标；异常内容留在原处并报告 |

</frozen-after-approval>

## Code Map

- `kimi_runtime.py` 的 `runtime_path`、`prepare_runtime`、`validate_runtime` 已实现源码资源路径、显式复制和固定版本/哈希校验。增加适合包根的验证接口，共享同一相对路径。保留原 CLI。
- `zgwd.spec` 在 `COLLECT` 合并两个 Analysis 的 TOC。准确收集单个已验证文件为 DATA，保留字节及固定版本目录，避免 PE 分析或 UPX 改写。生产入口为 `package_mc.ps1`。
- `package_mc.ps1` 在 `Update-PackageOutput` 前后分别调用 `Assert-PackageArtifacts`，更新会清理旧 runtimes。两处都应验证各自实际包里的文件；同时保留子进程退出码检查和 BuildOnly 行为。
- `tests/test_packaging_specs.py` 有 Windows 隔离假构建、实际 PowerShell 复制、用户数据哨兵和启动捕获。`tests/test_kimi_server_client_unit.py` 有显式准备与校验测试。`kimi_server_client.py` 无需改动。

## Tasks & Acceptance

- [x] source 端沿用既有显式 CLI，将已核验的本地文件准备到源码固定路径，作为后续本机默认构建输入。源码路径已有效时复用。更新 `docs/kimi-runtime.md` 记录新构建前置条件、命令和验收范围。
- [x] 实现 spec 收集与打包双重门禁。验证出错退出，不接受用户安装目录或 KIMI_BIN 代替缺失的包内文件。代码和自动测试不写入本机个人路径。
- [x] 扩展现有隔离测试，执行实际打包脚本和校验逻辑。可在夹具复制模块中换用测试 SHA 和受控版本探针；生产路径必须另用真实固定二进制验一次。缺失、错误哈希、正确哈希但错误版本分别断言失败原因。
- [x] 现场再次核验来源与目标；仅用准备 CLI 将已验证文件补入日常包。分别记录日常目标校验、隔离构建验证及云端问答状态，quick 审查限于本次改动。

**Acceptance Criteria**

- Given 输入已准备，When 执行默认 spec 的实际收集边界，Then 产物位于 `mc/_internal/runtimes/kimi/2.5.3/kimi.exe` 且通过真实验证。
- Given 暂存包缺失或内容不符，When 执行隔离更新，Then 返回失败、旧输出未变且无启动；Given 最终复制后损坏，When 执行最终门禁，Then 返回失败且无启动。
- Given 日常 MC 正在运行，When 仅显式补齐文件，Then 读回正确 SHA256 且独立执行版本为 2.5.3，原 MC 进程仍在。

## Implementation Notes

- `kimi_runtime.py` 统一生成固定相对路径。新增 `--package-root` 校验 `mc/_internal` 下的固定文件，拒绝借由文件链接或目录联接读取包外运行时。原显式准备入口保持已有目标保护。
- `zgwd.spec` 在 Analysis 前检查固定源码输入，并以单条 DATA 加入实际 COLLECT。PowerShell 用同一校验器独立检查暂存与最终输出，失败退出并阻止后续操作。源码二进制按路径加入 Git 忽略。

## Plan Change Log

## Review Triage Log

- dog ?? quick ???????????? AC ?????????????????????????????????? defer????????????

## Verification

- 运行相关 pytest 打包矩阵与 Kimi 运行时定向回归。测试应实际观察文件字节、退出码和启动记录。
- 使用独立临时 DistPath、WorkPath 和 BuildOnly 验证默认 spec；若无法执行完整隔离构建，实际执行默认 spec 的收集映射并用真实 PyInstaller 最小 COLLECT 验证字节，明确完整构建未验证。严禁用默认日常输出进行全量验证。
- 记录日常目标校验值与 MC PID/启动时间；云端问答与运行时验证分别报告。

### 执行结果

- 日常与源码固定路径原先均缺少文件。对本机候选先复核 SHA256，再执行独立 `--version`，随后用 `--source` 和 `--destination` 两次显式准备。原候选、源码和日常文件回读 SHA256 均为 `3fe337ab7aa742ba0b72cc87fcc9a267756e609a54358c0d85bbe200da9bec8d`，三处独立版本探针均为 2.5.3，日常包也通过 `--package-root`。补齐前后 `D:/code/cx/mc/mc.exe` 均为 PID 36616，启动时间 `2026-11-18T11:10:04.2390992+08:00`。
- 在非管理员 PowerShell 中执行原版 `package_mc.ps1`，使用独立临时 `DistPath`、`WorkPath` 和 `-BuildOnly`，默认 `zgwd.spec` 与 PyInstaller 6.22.3 完整 COLLECT 成功，脚本退出 0。暂存和最终输出均通过门禁。独立读取最终 `dist/mc/_internal/runtimes/kimi/2.5.3/kimi.exe`，确认包内仅一份、SHA256 同源码、版本为 2.5.3。
- 用同一默认 spec 的临时副本分别构造缺失和错误哈希的源码输入，环境同时提供有效 `KIMI_BIN`。实际 PyInstaller 均在 Analysis 前以 1 退出，给出对应原因及显式准备命令。
- `tests/test_packaging_specs.py`、`tests/test_kimi_server_client_unit.py` 和[此前的 13 项定向集成用例](plan-kimi-steer-rest-recovery-20261118.md#verification)共 185 项通过（19.69 秒）。打包回归运行实际 PowerShell、文件复制和校验 CLI。缺失、哈希错误、版本错误和联接替代均在更新前失败，旧产物、用户数据与启动记录通过检查。最终文件复制后损坏时返回失败且未启动。无效哈希和缺失文件均没有调用版本探针。
- 扩大运行到完整 `tests/test_kimi_integration.py` 后共 348 通过、3 失败。三个失败在未经本次修改的 `397d2dab52a062c5e00a51767d1f362507224e49` 隔离工作树逐项复现，涉及工具完成步骤、复用事件 ID 和中文执行步骤。
- 日常包只补入缺失运行时。本轮未向日常聊天发送探针，也未重新验收云端问答。
