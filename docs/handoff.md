# 当前交接

截至 2026-10-08。MC 双电脑远程身份配置已完成，功能提交为 `8290316`；RC 对应功能提交为 `a11d1bc`。本阶段源码、串行审查、问题修复和可达自动化验证完成，实际笔记本隧道、双公网连接、实体手机通知点击及 TalkBack 尚未验收。日常 MC 安装包与实体手机均未更新；源码提交不能证明已部署。

## 目标与当前实现

- 手机同时连接两台 MC：台式机固定 `default`，笔记本固定 `laptop`。每台电脑保留自己的地址与固定 token；笔记本不继承台式机凭据。MC 将身份保存到 `app_data_dir/remote_machine.json`，环境变量优先于持久值。操作、执行游标、已读与文件 URL 使用所属机器身份，详见 [远程配置](remote-control.md)。
- RC 的笔记本、台式机、全部标签严格过滤电脑聊天；全部显示设备后缀。无障碍标签包含成功收到 Android 通知后尚未阅读的消息数。正确详情成功显示回答及电脑端阅读清除相应范围；加载失败、仅划掉通知、切走或锁屏不算已读。本地 Doubao 保留独立入口。
- 原有已读协议仍以 pair/chat/generation 隔离，稳定 message_id 与首次成功完成的 answer_seq 关联。read_seq 单调，清空才换 generation；自动定位不确认阅读，主动导航、全文或成功网页打开确认冻结范围。Ctrl+Shift+X 的原生 MOD_NOREPEAT 与真实末项焦点保留。
- Kimi 系统注入边界修复、生成中公开思考与 commentary、REST 同 owner 补齐保留。边界仅按结构化 origin 判定；`metadata.origin` 存在时优先使用，未知值保守作为真实问题。完整旧验证记录已归档。
- F1 使用 owner/view/turn/revision 的真实可视尾页，visible 部分索引与后台补齐保留。发送受理和首次成功回复推进活动排序；读取、标题、失败和重放不置顶。
- Codex 私有 home 通过整目录链接复用全局 skills/plugins，新任务刷新原生清单，活动 turn 的追加输入不重启。打包脚本先临时构建和校验，再更新并自动启动最终产物；本阶段没有执行真实打包。

## 本阶段验证

四个审查层按用户要求逐个运行，23 条意见逐项核对，10 组修复完成；父代理复验另外发现并补齐缓存详情的已读确认。MC 最终 83 passed，1 个已在精确旧基线复现的无关用例排除。RC 定向 Flutter 118、文件/通知回归 104、实际详情/设置/删除 widget 14 项通过；Android 单元 56、专用模拟器 instrumentation 16 项通过；静态分析与空白检查通过。

两套私有 broker 的真实 Android UI Local 流程通过：同 chat ID 归属隔离、分别发送与显示答案、实际通知产生、非零设备及全部数字、对应缓存回答显示后清零、仅笔记本关闭/重启/重连。未调用真实模型或公网 Cloudflare，也未恢复 Windows 原生按键完整验收。

```powershell
# MC 源码目录；使用已配置的 Python 3.11 环境
.venv/Scripts/python.exe -m pytest tests/test_remote_machine_config.py tests/test_dual_local_harness.py tests/test_main_remote_nats_unit.py tests/test_remote_nats_protocol.py tests/test_remote_nats_unit.py tests/test_chat_read_state.py tests/test_execution_projection.py -q -k 'not archived_mobile_result_v2_outbox_keeps_interleaved_owners_and_unique_finals'
```

跨端命令及专用模拟器准备见 RC 的 `docs/current/testing.md`。本次只改文档，复用源码未变后的上述证据，未重复启动 GUI 或设备测试。工作区完整报告为 `D:/code/sj/_bmad-output/implementation-artifacts/dual-computer-control-verification.md`，最终 Local 证据目录为 `dual-local-root-read-final/`；这些含私有日志的工作区文件不进入产品 Git。

## 剩余事项与下一步

1. 获得实际笔记本及 Cloudflare 配置后，设置独立 named tunnel、DNS、`/nats` 和文件来源；填写固定 laptop 地址/token，再验证双公网链路。不得编造域名或使用台式机凭据顶替。
2. 后续明确部署时，核对实际安装版本、数据路径与 OneDrive 衔接，再按 `package_mc.ps1` 和 RC 实体 ARM64 安装脚本交付；保留聊天、笔记、文件及运行数据，默认不备份旧程序包。
3. 实体通知点击、TalkBack 数字朗读与焦点、完整同版本 Live 验收仍需实际设备。真实隧道下更换笔记本凭据时的在途命令/文件归属建议重点复核。
4. 用户已停止完整桌面验收，未经后续授权不得恢复。旧整轮已读验收没有通过；实际解锁夹具和中文声音听验未完成，不能拼接不同失败轮形成成功结论。
5. Kimi 旧失败记录未改动；安装包曾只读确认缺少新注入边界函数。部署后的安全恢复须先核对原始 owner，不能重发问题或直接改库冒充验证。

## 数据、环境与避坑

- 本次不修改 OneDrive 笔记或常用命令同步。两台 MC 可以保持连接；用户保证不同时写入共享数据。换机写入前等待同步，不能用旧库直接覆盖新数据。
- 源码笔记为 `D:/code/note/notes.db`；打包版需要个人 OneDrive 的 `code/data/sj/notes.db` 已存在且通过完整性与表结构校验。2026-09-29 的云端快照不含之后所有变化；切包前核对实际路径及后续数据，测试注入临时路径。
- 2026-10-08 专用 AVD 为 `Codex_Dual_Control_API_35`，当次 serial 为 `emulator-5570`。复跑先核对设备归属；只可重置专用 QA 包。真实系统权限弹窗会让 Flutter inactive，不能绕过生产阅读守卫。
- Local harness 的 history/state 必须含真实 ChatStore 的 canonical 回答及 read_state 元数据；通知 ACK 不代表详情可确认已读。私有 Flutter 日志含凭据，不能原样发布。
- MC `main` 跟踪 `origin/main`；RC `master` 跟踪 `origin/master`。本次 neat-freak 将功能及文档提交正常推送既有 GitHub 上游；推送结果按实际命令与 `git log -1` 核对，不代表安装完成。

旧阶段的精确命令、现场 owner 与失败证据见 [2026-10-08 前交接归档](archive/handoff-before-dual-control-2026-10-08.md)；长期规则见 [经验](experience.md)、[反思](reflection.md)与 [文档索引](README.md)。
