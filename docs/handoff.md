# 当前交接

截至 2026-10-09。双电脑远程控制已部署到笔记本 MC 和实体 ARM64 手机：笔记本独立隧道、自动机器名识别、手机到两台电脑的公网连接及各一次真实模型问答通过。实体通知点击、文件下载、单机断连恢复和 TalkBack 听验尚未验收。实测细节见工作区 `D:/code/sj/_bmad-output/implementation-artifacts/dual-physical-deployment-20261009.md`。

## 目标与当前实现

- 手机同时连接两台 MC：`emperorComputer` 自动识别 `default`，`emperorLaptop` 自动识别 `laptop`；显式 pair 环境变量优先，未知计算机名沿用持久值或 `default`。每台电脑保留自己的地址与固定 token，跨身份不继承凭据。MC 将身份保存到 `app_data_dir/remote_machine.json`。操作、执行游标、已读与文件 URL 使用所属机器身份，详见 [远程配置](remote-control.md)。
- RC 的笔记本、台式机、全部标签严格过滤电脑聊天；全部显示设备后缀。无障碍标签包含成功收到 Android 通知后尚未阅读的消息数。正确详情成功显示回答及电脑端阅读清除相应范围；加载失败、仅划掉通知、切走或锁屏不算已读。本地 Doubao 保留独立入口。
- 原有已读协议仍以 pair/chat/generation 隔离，稳定 message_id 与首次成功完成的 answer_seq 关联。read_seq 单调，清空才换 generation；自动定位不确认阅读，主动导航、全文或成功网页打开确认冻结范围。Ctrl+Shift+X 的原生 MOD_NOREPEAT 与真实末项焦点保留。
- Kimi 系统注入边界修复、生成中公开思考与 commentary、REST 同 owner 补齐保留。边界仅按结构化 origin 判定；`metadata.origin` 存在时优先使用，未知值保守作为真实问题。完整旧验证记录已归档。
- F1 使用 owner/view/turn/revision 的真实可视尾页，visible 部分索引与后台补齐保留。发送受理和首次成功回复推进活动排序；读取、标题、失败和重放不置顶。
- Codex 私有 home 通过整目录链接复用全局 skills/plugins，新任务刷新原生清单，活动 turn 的追加输入不重启。打包脚本先临时构建和校验，再更新并自动启动最终产物；用户已自行重包并手动重启日常 MC。当前日常 MC 的后续退出/重启必须由用户本人操作，不能由 agent 自动进行。`package_mc_current_temp.ps1` 是本地未跟踪临时脚本，本轮不提交或删除。

## 本阶段验证

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
