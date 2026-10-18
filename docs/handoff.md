# 当前交接

## 2026-10-18 可携带网页入口实现

已集成 `browser_open.py`、随包 `mc-browser-open` 技能、技能根合并刷新与 `MC_BROWSER_OPEN_ARGV`。源码调用 `.venv/Scripts/python.exe browser_open.py --url <HTTP(S) URL>`；默认 onedir 包使用 `mc_worker.exe --browser-open --url <URL>`，不依赖系统 Python。helper 保留专用实例与已有标签，只创建/续验自己的 target；失败返回 targetId 后只用同 URL 加 `--target-id` 续验，不能盲目重建。

新配置和旧无版本配置默认 `auto-review`，原生三元组为 `on-request / workspace-write / auto_review`。状态标记 `codex_approval_defaults_version: 1` 只迁移一次，此后菜单手选保留，不重启活动 turn。真实审核拒绝立即停止，不降级审批或代答 accept。

定向验证：核心 helper/client/worker/packaging 原 164 项分段通过（163 项通过、1 项旧预期修正后精确复验通过）；审批迁移、菜单、IPC 串行 50 项通过。helper 真实执行问题修复后曾有 27 项通过，审查局部补丁完成后 director 与 engineer 最终均复验 46 项通过。engineer 已完成最终非管理员隔离重构建（exit 0）及打包 worker `--browser-open --help`。

含空格源码目录的原生技能发现、自然语言读取随包技能并调用 helper、真实 `auto_review approved` 已实测；源码首次网页 complete、可见且非最小化，但 `foreground=false`，helper 正确报错，该次不记为置前成功。engineer 已核对最终重构建包真实 `commandExecution` exit 0、随包技能与用户技能并存，同一自有 target 续验 `status=ok / complete / visible=true / minimized=false / foreground=true`、`HWND == foregroundHwnd` 且 `preexistingTargetsPreserved=true`，真实自动审查为 approved；证据见仓库外最终报告 `D:/code/sj/_bmad-output/implementation-artifacts/verify-portable-browser-open-engineer.md`。首次全新 profile 启动、另一台电脑的 sandbox 前置状态及焦点行为、正式 MC GUI 自然语言链仍待实际验收，本机隔离验证不能替代。以下旧真实浏览器证据只对应仓库外验证宿主，不代表本实现已通过。

私有诊断证据已移至仓库外 `D:/code/sj/_bmad-output/implementation-artifacts/browser-open-fixes/`，不提交产品仓库。

独立构建使用独立 `-DistPath`、`-WorkPath` 和 `-BuildOnly`；不覆盖、退出或重启日常 MC，不修改生产库/凭据，不关闭共享 Chrome。profile、Cookie、认证及私有日志不入包。旧单文件 spec 不在本功能支持范围。

## 历史计划快照：2026-10-18 实施前状态

用户已确认实施范围：另一台 Windows 电脑已有 Codex、Chrome 和 MC；希望拉取代码并重新打包后，直接向 MC 下达“打开某网址并显示到前台”的指令即可使用，无需额外插件、仓库外脚本或手动选择审批模式。本会话只完成分析、计划和本机验证，**下列集成尚未实现**。

实施依据为 dog 已更新的[实施计划](../../_bmad-output/implementation-artifacts/plan-portable-browser-open.md)，绝对路径 `D:/code/sj/_bmad-output/implementation-artifacts/plan-portable-browser-open.md`。计划位于 MC 仓库外，当前电脑的新会话可直接读取，不能假定另一台电脑仅拉取产品仓库就有该文件。已有方案明确，下一会话直接按计划实现，不重复广泛调研。沿用此前 `bmad-build-auto` 工作方式及项目派遣规则，director 实现、engineer 独立验证；本轮没有启动新的实施流程。

### 已完成及证据边界

- MC 源码已提交 `8bf51069fe69eaf410664badebe56338c590581b`（支持三种审批模式），此功能提交尚未推送，远端状态接续时再核对。本次交接另作本地文档提交，保持接续工作区干净。它只接入自动审查，**没有随包浏览器 helper/技能，也没有更改默认 never**。用户随后自行重新打包并启动 MC，agent 未操作其退出/重启；当前进程信息接续时重新读取，不复用历史 PID。
- 本机真实自动审查曾返回 `approved`，无需人工点击，已验证网页完整加载、专用 Chrome 窗口可见/非最小化、真实前台 HWND 与归属一致，并打开了腾讯云登录页。证据在工作区 `D:/code/sj/_bmad-output/implementation-artifacts/verify-visible-browser-e2e-final.md`、`open-tencent-console-result.json`。验证使用仓库外脚本及独立 Codex 会话，不等于跨机产品能力已交付；验证宿主还使用过进程级 `windows.sandbox="elevated"`，不能直接照搬为跨机默认配置。
- 成功验证代码可作为参考：工作区同目录的 `visible-browser-cdp-helper.py`、`open-tencent-console.ps1`；它们包含本机路径、PID、时间戳、固定 URL，必须提炼，不可直接当产品依赖。`run-open-tencent-console.py` 等独立模型验证宿主也不能移入正式功能。全局 `web-access` 目录此前为空，不依赖 legacy 副本、不改写全局技能。

### 下一步实施顺序

1. **默认官方自动审查及一次迁移。** 用户最新明确要求默认启用，取代旧计划的“保留默认 never”。新安装、缺失/未知配置以及旧配置首次升级均采用 `auto-review`，原生实际三元组为 `on-request / workspace-write / auto_review`；不能向 API 发送无效原生策略 `auto-review`。用 `codex_approval_defaults_version: 1` 做最小一次迁移，旧标记缺失时应用新默认；之后用户主动选择 never/on-request 必须持久保留，不每次启动覆盖。覆盖初始化、load/save、getattr fallback、菜单、IPC 缺值及直接 start/resume；加载恢复完整状态后再保存迁移，不能覆盖尚未恢复的数据。只影响后续新建/恢复，不改变活动回合。
2. **新增正式浏览器入口。** 按计划新增 `browser_open.py` 和 `assets/codex-skills/mc-browser-open/SKILL.md`，让 Codex 从自然语言发现并调用。源码/安装包运行态生成准确 argv，安装包复用 `mc_worker.exe --browser-open --url …`；`worker_launcher.py` 提前分派，不启动模型 worker 协议。helper 本身不启动新的 Codex 子会话、不代答 accept。
3. **接入发现和打包。** `codex_client.py` 合并用户技能根与随包技能根并刷新，启动环境提供 `MC_BROWSER_OPEN_ARGV`，不覆盖原用户根；资源按源码目录/`sys._MEIPASS` 定位。`zgwd.spec` 收录技能与已有 websocket-client 依赖，`package_mc.ps1` 补必要产物检查。所有路径、Chrome 安装位置、PID/StartTime、targetId/HWND 动态解析，不依赖本机盘符、外部 Python 或 `_bmad-output`。
4. **定向验证后交付。** 验证旧配置迁移及用户后来手选模式保留、真实原生技能发现和自然语言调用；覆盖源码换目录、隔离打包版、专用 Chrome 首次启动与后续复用、网页及真实前台状态。另一台电脑的首启和焦点效果必须在该机实际验收，不能用本机结果代替。必要产品代码/技能/打包配置/测试/短说明提交并推送，包含现有未推送的自动审查提交；浏览器 profile、Cookie、认证文件和私有诊断日志不上传。

### 实现中必须保留的已验证要点

- 独立 profile `%LOCALAPPDATA%/ChromeAutomationProfile`，调试仅 `127.0.0.1:9222`；启动带 `--no-first-run`、`--no-default-browser-check`。欢迎/账号窗口不是普通标签窗口，统一用 CDP `Target.createTarget(newWindow:true)` 创建本任务普通窗口。保留日常 Chrome 和全部既有标签，只操作自己创建的 target。
- 成熟 Python websocket-client 传输优先于临时 PowerShell WebSocket 拼装；原始 CDP 错误完整保留。PowerShell JSON 集合包装和错误编码曾造成误判，不能照搬失败实现。创建成功先返回自有 targetId，后续失败只续验该目标，不盲目重复建页；根进程排除 `--type=` 子进程。最终必须验证实际前台 HWND/PID、可见和非最小化，不凭 SetForegroundWindow 返回值或进程 exit 0 报成功；不写死 h1/测试站内容。
- WMI `HRESULT 0x80041003` 等普通沙箱失败不等于审核拒绝；只有当前真实工具及权限允许时，走正式升级请求交给 auto_review。实际审核 deny/policy blocked 后停止，不换工具、线程或编码绕过，不降级成 MC 无条件放行。新机 Windows 原生沙箱是否就绪需检查，不自动改全局配置或承诺所有系统焦点条件均能置前。
- **不退出、重启或覆盖日常 MC，不修改生产聊天库/配置/凭据。** 实施验证使用隔离数据和独立测试实例；若构建隔离包，使用独立 `-DistPath` 与 `-BuildOnly`，不更新当前运行目标。实际运行包的替换和重启仍由用户本人安排；不在本任务顺带修改 Cloudflare、frp、云安全组或 DNS。

以下为此前远程控制功能的历史交接，其部署与测试边界仍有效。

截至 2026-10-18 整理，最近实体验证为 2026-10-17；主分支为 `main`。远程数据精简已实现独立临时 RPC、有限通知流、权威通知快照和按需分页，生产 broker 使用独立 `mc`/`mobile` 用户及角色权限，见 [协议](remote-slim-protocol.md)。手机的新 `mobile/password` 认证与旧运行包的 `auth_token` 模式不兼容，必须配套升级；同 token 的两种认证实测已确认该根因，不能只延长手机超时或更换 token。

2026-10-17 用户已自行重新打包并启动 `D:/code/cx/mc`。手机此前仍使用 CZ 临时公网地址，CZ MC 已退出，导致无法连接。已使用正式私有参数保数据覆盖安装实体 ARM64 RC，恢复 `wss://laptop.tingyou.cc/nats`；两来源 token、台式机地址和两来源 backup 均未变，最终选择笔记本。CX 直连 18081、桥接 18080、正式公网三处认证/PONG 的 server_id 一致，实际手机显示笔记本已连接和 CX 聊天列表；手机应用退出重开后自动恢复。此次未重启 CX 或改隧道，未发送模型请求。CZ 的独立数据副本保留为历史测试，不代表当前连接目标。

`package_mc.ps1 -BuildOnly -DistPath <独立父目录>` 构建并更新该目录下的 `mc`，不自动启动；它不是仅生成归档的开关，仍拒绝更新正在运行的目标。默认不传 `-BuildOnly` 时保留成功后自动启动一次的行为。原目录更新保留 `history/remote_machine.json` 中同身份配置；新目录不自动继承凭据。切回正式 `cx` 时，用户本人先退出旧 MC，再打包；手机现已恢复正式私有构建；后续升级继续使用正式参数，不能仅依赖持久偏好，因为完整启动构建参数优先。跨目录部署须迁移同身份配置并核对隧道实际指向，不能同时让两个同身份实例争用正式入口。

本轮验证：MC 认证/打包定向 25 项、实例键及启动数据预检 6 项；RC 原生 13 项、Flutter 107 项、分析通过；最终双来源隔离 Local UI 39 秒通过，实体 ARM64 使用 `adb install -r -t` 升级且设备 hash 匹配。`cz` 本地 WS 与临时公网 WSS 的 server_id 一致，hello/readiness/列表/详情均通过。完整证据在工作区 `_bmad-output`，含私有配置及日志的材料不进入 Git。

待办：台式机部署状态复核、当前版本真实模型问答、锁屏通知/文件/TalkBack 听验及长时弱网观察。2026-10-17 曾有一次瞬态传输/已读协调超时，重开后恢复；短时通过不证明长期稳定。不要退出日常 MC、清历史或回退到旧认证掩盖故障。以下 2026-10-09/12 记录仅保留为历史背景。

截至 2026-10-09。双电脑远程控制已部署到笔记本 MC 和实体 ARM64 手机：笔记本独立隧道、自动机器名识别、手机到两台电脑的公网连接及各一次真实模型问答通过。实体通知点击、文件下载、单机断连恢复和 TalkBack 听验尚未验收。实测细节见工作区 `D:/code/sj/_bmad-output/implementation-artifacts/dual-physical-deployment-20261009.md`。

## 目标与当前实现

- 手机同时连接两台 MC：`emperorComputer` 自动识别 `default`，`emperorLaptop` 自动识别 `laptop`；显式 pair 环境变量优先，未知计算机名沿用持久值或 `default`。每台电脑保留自己的地址与固定 token，跨身份不继承凭据。MC 将身份保存到 `app_data_dir/remote_machine.json`。操作、执行游标、已读与文件 URL 使用所属机器身份，详见 [远程配置](remote-control.md)。
- RC 的笔记本、台式机、全部标签严格过滤电脑聊天；全部显示设备后缀。无障碍标签包含成功收到 Android 通知后尚未阅读的消息数。正确详情成功显示回答及电脑端阅读清除相应范围；加载失败、仅划掉通知、切走或锁屏不算已读。本地 Doubao 保留独立入口。
- 原有已读协议仍以 pair/chat/generation 隔离，稳定 message_id 与首次成功完成的 answer_seq 关联。read_seq 单调，清空才换 generation；自动定位不确认阅读，主动导航、全文或成功网页打开确认冻结范围。Ctrl+Shift+X 的原生 MOD_NOREPEAT 与真实末项焦点保留。
- Kimi 系统注入边界修复、生成中公开思考与 commentary、REST 同 owner 补齐保留。边界仅按结构化 origin 判定；`metadata.origin` 存在时优先使用，未知值保守作为真实问题。完整旧验证记录已归档。
- F1 使用 owner/view/turn/revision 的真实可视尾页，visible 部分索引与后台补齐保留。发送受理和首次成功回复推进活动排序；读取、标题、失败和重放不置顶。
- Codex 私有 home 通过整目录链接复用全局 skills/plugins，新任务刷新原生清单，活动 turn 的追加输入不重启。打包脚本先临时构建和校验，再更新并自动启动最终产物；用户已自行重包并手动重启日常 MC。当前日常 MC 的后续退出/重启必须由用户本人操作，不能由 agent 自动进行。`package_mc_current_temp.ps1` 是本地未跟踪临时脚本，本轮不提交或删除。

## 本阶段验证

2026-10-12 分支收尾定向回归：`.venv/Scripts/python.exe -m pytest tests/test_remote_machine_config.py tests/test_remote_readiness.py tests/test_remote_readiness_qa.py -q`，24 项通过；仅纯配置与协议测试，没有恢复已停止的完整桌面验收。

四个审查层按用户要求逐个运行，23 条意见逐项核对，10 组修复完成；父代理复验另外发现并补齐缓存详情的已读确认。MC 最终 83 passed，1 个已在精确旧基线复现的无关用例排除。RC 定向 Flutter 118、文件/通知回归 104、实际详情/设置/删除 widget 14 项通过；Android 单元 56、专用模拟器 instrumentation 16 项通过；静态分析与空白检查通过。

两套私有 broker 的真实 Android UI Local 流程通过：同 chat ID 归属隔离、分别发送与显示答案、实际通知产生、非零设备及全部数字、对应缓存回答显示后清零、仅笔记本关闭/重启/重连。其后 2026-10-09 实体手机经双公网真实发送各一次：笔记本 canonical `LAPTOP_OK_10`（UI 当时显示空格形式）、台式机 UI `DESKTOP OK 10`；两来源 owner 独立，重新打开笔记本详情后未读由 1/1 变为 0/1。笔记本自动识别的定向测试 director 31 pass/1 deselected、startup 3 pass，engineer 再核 31 pass。未恢复 Windows 原生按键完整验收。

```powershell
# MC 源码目录；使用已配置的 Python 3.11 环境
.venv/Scripts/python.exe -m pytest tests/test_remote_machine_config.py tests/test_dual_local_harness.py tests/test_main_remote_nats_unit.py tests/test_remote_nats_protocol.py tests/test_remote_nats_unit.py tests/test_chat_read_state.py tests/test_execution_projection.py -q -k 'not archived_mobile_result_v2_outbox_keeps_interleaved_owners_and_unique_finals'
```

跨端命令及专用模拟器准备见 RC 的 `docs/current/testing.md`。上述 2026-10-08 Local 验证复用当时证据；2026-10-09 新增机器名识别定向测试和实体实测另如前述，本次文档整理未重复启动 GUI 或设备测试。工作区完整报告为 `D:/code/sj/_bmad-output/implementation-artifacts/dual-computer-control-verification.md`，最终 Local 证据目录为 `dual-local-root-read-final/`；这些含私有日志的工作区文件不进入产品 Git。

## 剩余事项与下一步

1. 笔记本独立 named tunnel `laptop.tingyou.cc` 已配置；手机两套私有地址与 token 已部署。笔记本新 MC 使用 18080 桥接到新 NATS 18082（旧进程当时占 18081）；直连、桥接、公网三处 `INFO`/`PING/PONG` 的 server ID 一致。PID、端口占用、手机 serial 是 2026-10-09 快照，接续时重新读取。
2. 实体通知点击、文件授权下载、单机断连恢复、TalkBack 数字朗读与焦点，以及完整同版本 Live 验收仍未完成；已通过的两条真实模型问答不覆盖这些项目。用户已自行卸载旧手机包后才安装新 ARM64 包，因此本轮不能宣称完成保数据升级验收。
3. 不自动重启日常 MC；涉及配置生效由用户自己重启。真实隧道下更换笔记本凭据时的在途命令/文件归属仍可专项复核。
4. 用户已停止完整桌面验收，未经后续授权不得恢复。旧整轮已读验收没有通过；实际解锁夹具和中文声音听验未完成，不能拼接不同失败轮形成成功结论。
5. Kimi 旧失败记录未改动；安装包曾只读确认缺少新注入边界函数。部署后的安全恢复须先核对原始 owner，不能重发问题或直接改库冒充验证。

## 数据、环境与避坑

- 本次不修改 OneDrive 笔记或常用命令同步。两台 MC 可以保持连接；用户保证不同时写入共享数据。换机写入前等待同步，不能用旧库直接覆盖新数据。
- 源码笔记为 `D:/code/note/notes.db`；打包版需要个人 OneDrive 的 `code/data/sj/notes.db` 已存在且通过完整性与表结构校验。2026-09-29 的云端快照不含之后所有变化；切包前核对实际路径及后续数据，测试注入临时路径。
- 2026-10-08 专用 AVD 为 `Codex_Dual_Control_API_35`，当次 serial 为 `emulator-5570`。复跑先核对设备归属；只可重置专用 QA 包。真实系统权限弹窗会让 Flutter inactive，不能绕过生产阅读守卫。
- Local harness 的 history/state 必须含真实 ChatStore 的 canonical 回答及 read_state 元数据；通知 ACK 不代表详情可确认已读。私有 Flutter 日志含凭据，不能原样发布。
- MC `main` 跟踪 `origin/main`；RC `master` 跟踪 `origin/master`。本次 neat-freak 将功能及文档提交正常推送既有 GitHub 上游；推送结果按实际命令与 `git log -1` 核对，不代表安装完成。

旧阶段的精确命令、现场 owner 与失败证据见 [2026-10-08 前交接归档](archive/handoff-before-dual-control-2026-10-08.md)；长期规则见 [经验](experience.md)、[反思](reflection.md)与 [文档索引](README.md)。
