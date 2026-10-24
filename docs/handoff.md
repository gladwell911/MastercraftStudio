# 当前交接

更新日期：2026-10-24。源码仓库 `D:/code/sj/mc`，分支 `main` 跟踪 `origin/main`。本阶段完成长期运行性能与产物治理，以及新dog审查提出的7项补丁。此前提交为 `5ec2500`（笔记初始化）和 `21fa568`（性能治理）；本次neat-freak收尾范围包含剩余补丁与文档；最终哈希和推送结果以Git实测为准。

本轮没有重新打包、清理日常数据或退出/重启 `D:/code/cx/mc/mc.exe`。源码、GitHub和运行中EXE是不同状态。

## 已完成

- 聊天状态缺失、损坏或有效时，笔记控制器均在 `_load_state()` 后恢复并刷新初始列表；对应计时器夹具负责完整回收。
- pending outbox部分索引消除空查询扫描已发布历史；独立 `publication_progress` 使确认水位在清理、重复ACK和重启后保持单调，不跨过未确认前序。权威事实、用户历史、会话和笔记不清理。
- 清理先取安全前沿内的有界候选，再筛七天年龄。自动维护在既有worker取批次前执行，修复维护等待期间新通知的交接遗漏；无新轮询。规则、开关和操作命令见[长期运行维护](runtime-maintenance.md)。
- NATS/Cloudflared自有日志持续轮转、故障后恢复写入并继续排空管道；正确处理短写及零进展，导航诊断默认关闭。正常Windows后代持有stdout时，reader/pipe/file关闭已由真实fixture验证。
- 构建work/staging进入开发目录，收集和最终复制边界排除开发/运行数据；修复tests祖先误筛必要资源，保留自定义构建路径、必要资产及已有用户数据。benchmark拒绝不存在pair，明确区分全库与所选pair计数；修复Cloudflared PIPE测试替身。
- 新dog四个独立视角审查完成，7项行动已修复并独立验证；1项未验证风险保留。代理子树额度受限时，本轮由主线程逐个创建fresh审查员完成，未复用上下文充数或修改配置/历史。

## 验证与性能证据

- 性能治理最终八文件串行 **242 passed、2 deselected**；只排除下列两个精确父版本亦失败的用例，不把它们计为通过。
- 最新7项补丁独立验证 **51 passed**：三个完整受改文件48项，Cloudflared精确启动及两个日志消费者3项；无skip/xfail。Windows真实后代测试实际执行，复制矩阵使用临时假构建器，未真实部署。
- 本次收尾补跑笔记启动恢复对应的 `tests/test_notes_ui_automation.py`，**7 passed**；文档一致性检查覆盖14份知识文件、62个本地链接，断链为0。上述51项沿用源码未变时的独立验证结果。
- 真实备份只读复制后，196167条已发布记录的两个lane空查询p95从259.03/295.45 ms降至3.80/1.27 ms。最终合成196167/1961670行分别约1.10–1.29 ms；完整迁移elapsed149.40/1770.22 ms，不是写锁时长。历史一轮合成样本仅8.65倍改善仍保留，不以其他轮次覆盖它。
- 大量近期记录和旧记录高于低安全水位两种场景，清理VM步数不随总历史线性增加；同时限制检查量与删除量，而不只检查LIMIT返回条数。

在源码目录复验最新补丁：

```powershell
.venv/Scripts/python.exe -m pytest tests/test_nats_runtime.py tests/test_packaging_specs.py tests/test_benchmark_outbox.py --noconftest -o pythonpath=. -q -ra
.venv/Scripts/python.exe -m pytest tests/test_main_unit.py::test_managed_cloudflared_starts_supplied_token_file_command_and_releases_lease tests/test_main_remote_nats_unit.py::test_cloudflared_channels_survive_continuous_rotation tests/test_main_remote_nats_unit.py::test_navigation_diagnostics_default_off_and_bounded_when_enabled -q
```

完整先前回归与性能命令见[协议](remote-slim-protocol.md)及[维护说明](runtime-maintenance.md)。wx套件串行，frame测试注入临时笔记路径；不为文档收尾重复无改动的宽回归。

本机原始证据在仓库外 `D:/code/sj/_bmad-output/implementation-artifacts/`：`plan-mc-long-run-performance.md`、`review-mc-long-run-performance-new-dog.md`、`verify-mc-new-dog-seven-patches-engineer.md`、`verify-mc-long-run-performance-engineer.md`、`mc-outbox-benchmark-archive-engineer.json`、`mc-outbox-benchmark-final-patch-engineer.json`。私有诊断、备份、临时辅助脚本不进产品Git。逐轮细节和原部署背景见[收尾前快照](archive/handoff-before-runtime-maintenance-wrapup-2026-10-24.md)。

## 剩余问题与下一步

1. **延期风险，未证实泄漏**：`owned_logs.py` 中reader若正在写盘，单次 `CancelSynchronousIo` 可能错过随后阻塞读取。需要真实Windows后代持stdout、受控读/写/关闭交错才能裁决；本轮7项补丁未扩修该风险。正常后代关闭通过不能代替这一调度测试。
2. 两个已有基线失败仍在：`test_archived_mobile_result_v2_outbox_keeps_interleaved_owners_and_unique_finals` 缺chat-a；`test_registered_history_handler_bounds_escaped_long_content_and_preserves_older_cursor` 的answer_seq旧预期。不得因只跑排除后的集合而宣称全套通过。
3. 需要安装验证时，先核对实际EXE版本、数据路径及私有启动入口，由用户退出日常MC，再按[远程配置](remote-control.md)对应机器部署；不因本次源码完成而自动打包/启动。
4. 需要定位剩余卡顿时，测同一操作的UI延迟、数据库等待及24–72小时资源曲线，再决定是否修改历史保存/文件列表。尚无证据证明文件数量是唯一原因，也未建立月级流畅度结论。
5. 当前通知流上限保留；legacy流TTL和Codex上游存储未获充分恢复证据，不手删或虚构关闭配置。

## 其他有效背景与约束

- 2026-10-19日常台式机default通过external/frp/Caddy与实体RC连通；TLS握手、恢复期发送错误和前台回复延迟仍待修。RC接续文档为本机 `D:/code/sj/rc/docs/current/2026-10-19-connection-issues.md`；不要把通知快等同于详情快或拼接不同事件解释根因。
- 2026-10-20笔记本FRP私有bundle准备完成、服务器独立端口/路由已配置，本机部署及认证/列表仍待验证。手机已安装内置双FRP配置的版本不证明笔记本已上线；保留default来源，下一步按[笔记本部署](../deploy/mc-relay/LAPTOP.md)现场核实。
- 双来源固定台式机default、笔记本laptop，已知计算机名自动识别、显式pair优先；不互相继承凭据。笔记、OneDrive和已读隔离规则未改，两台MC不并发写共享笔记。
- 源码笔记 `D:/code/note/notes.db`；打包版要求现存、完整且表结构正确的个人OneDrive `code/data/sj/notes.db`。2026-09-29快照不能覆盖之后改动，切包前核对与补齐；测试只写注入路径。
- 可携带浏览器入口、默认自动审查、全局skills/plugins整目录链接和原生发现刷新已实现；首次专用profile、正式GUI全链、另一台电脑仍待验。目标续验保持原target/window，真实审核拒绝后停止。
- Kimi结构化注入边界、owner/generation隔离、canonical执行投影和已读语义保留；详见[文档索引](README.md)。用户此前停止的完整桌面验收不在本次恢复范围。
