# 当前交接

## 2026-10-18 可携带网页入口实现

已集成 `browser_open.py`、随包 `mc-browser-open` 技能、技能根合并刷新与 `MC_BROWSER_OPEN_ARGV`。源码调用 `.venv/Scripts/python.exe browser_open.py --url <HTTP(S) URL>`；默认 onedir 包使用 `mc_worker.exe --browser-open --url <URL>`，不依赖系统 Python。helper 保留专用实例与已有标签，只创建/续验自己的 target；失败返回 targetId 后只用同 URL 加 `--target-id` 续验，不能盲目重建。

新配置和旧无版本配置默认 `auto-review`，原生三元组为 `on-request / workspace-write / auto_review`。状态标记 `codex_approval_defaults_version: 1` 只迁移一次，此后菜单手选保留，不重启活动 turn。真实审核拒绝立即停止，不降级审批或代答 accept。

定向验证：核心 helper/client/worker/packaging 原 164 项分段通过（163 项通过、1 项旧预期修正后精确复验通过）；审批迁移、菜单、IPC 串行 50 项通过。helper 真实执行问题修复后曾有 27 项通过，审查局部补丁完成后 director 与 engineer 最终均复验 46 项通过。engineer 已完成最终非管理员隔离重构建（exit 0）及打包 worker `--browser-open --help`。

含空格源码目录的原生技能发现、自然语言读取随包技能并调用 helper、真实 `auto_review approved` 已实测；源码首次网页 complete、可见且非最小化，但 `foreground=false`，helper 正确报错，该次不记为置前成功。首个隔离包模型回合确认随包技能与用户技能并存，同 target 续验命令 exit 0，并有真实自动审查 approved。审查补丁后的最终重构建包由 engineer 直接调用同一自有 target 续验，exit 0，返回 `status=ok / complete / visible=true / minimized=false / foreground=true`、`HWND == foregroundHwnd` 且 `preexistingTargetsPreserved=true`；这次没有新的模型审核，不将此前批准冒充本次触发。证据见仓库外最终报告 `D:/code/sj/_bmad-output/implementation-artifacts/verify-portable-browser-open-engineer.md`。首次全新 profile 启动、另一台电脑的 sandbox 前置状态及焦点行为、正式 MC GUI 自然语言链仍待实际验收，本机隔离验证不能替代。

私有诊断证据已移至仓库外 `D:/code/sj/_bmad-output/implementation-artifacts/browser-open-fixes/`，不提交产品仓库。

独立构建使用独立 `-DistPath`、`-WorkPath` 和 `-BuildOnly`；不覆盖、退出或重启日常 MC，不修改生产库/凭据，不关闭共享 Chrome。profile、Cookie、认证及私有日志不入包。旧单文件 spec 不在本功能支持范围。

### 下一步与接续

1. 已完成实现以提交 `57206cfe18134ee8e2e649905047ea1b09443b5e` 及[仓库内实施记录](../_bmad-output/implementation-artifacts/plan-portable-browser-open.md)为准，不再重复实施旧计划。2026-10-18 neat-freak 收尾同步 README、交接、反思和经验；推送结果以实际远端 SHA 为准。
2. 在另一台电脑拉取后，使用该机实际目录重新打包；退出和重启日常 MC 仍由用户本人完成。依次检查有效审批模式、技能发现、首次专用 profile 启动及真实前台，不能仅凭命令 exit 0 通过。
3. 本机尚未覆盖正式 MC GUI 自然语言全链。若续验失败，保留 targetId，仅续验原目标；`continuationAvailable=false` 时停止，不新建目标。首次 HWND 绑定要求唯一，已确认目标允许无关窗口共存，但原 HWND 和 CDP windowId 必须保持一致。
4. 本轮只改文档并复用对应版本的已有验证，未重新打包、操作手机、启动模型或修改远程协议。RC 本轮无功能改动，其远程验收待办继续以下文和 RC 交接为准。

## 远程控制当前状态

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
