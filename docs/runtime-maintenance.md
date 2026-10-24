# 长期运行维护

截至2026-10-24的源码行为。Git更新不更新正在运行的MC；本轮没有打包、清生产数据或重启日常程序。部署边界见[当前交接](handoff.md)，远程恢复合同见[协议](remote-slim-protocol.md)。

## 数据与日志保留

| 内容 | 当前策略 |
|---|---|
| `history/chat_history.db` 的 `publication_outbox` | `publication_progress` 独立保存 pair/domain 发布前沿。已发布、无阻塞且不超过前沿的副本，至少保留七天后才可清理；权威 `durable_facts`、聊天、已读和通知恢复数据保留。 |
| 一次维护的工作量 | 默认先检查至多500条安全候选，硬上限1000，再筛年龄；近期前缀可能延后后面的旧记录。争锁立即退让，不执行VACUUM。删除释放内部页，不保证文件立即缩小。 |
| 自动维护 | 活跃发布worker在取批次前检查，最多每300秒处理一个domain的一批，无新增轮询线程。没有worker活动就不清理；这不是数据库总容量上限。 |
| MC拥有的NATS、Cloudflared日志 | 持续读取管道并轮转，每个日志5 MiB加3个备份。启动时旧超大自有日志保留有界尾部；异常仍排空管道，现有诊断尾部显示安全计数 `log_write_failures=N`。 |
| 导航调试日志 | 默认关闭；显式开启时同样轮转。 |
| Codex日志库、会话、上传附件、旧开发产物 | 本轮不自动清理或停用，避免破坏恢复和用户内容；没有引入未经证实的Codex配置。 |
| NATS流 | 通知流原有限制为七天、32 MiB、20000条；legacy COMMAND/EVENT未增加TTL，不能将当前客户端恢复能力当作全部旧客户端的证明。 |

发布确认仅表示broker确认，不表示手机已消费或已读。不要手删 `durable_facts`、运行中的SQLite/WAL文件或整个 `jetstream` 目录。

## 维护开关

在启动新版本MC前设置环境变量；当前已运行进程不会继承另一个终端新设置的环境。

```powershell
# 暂停后续自动outbox维护，不影响发布与权威回放。
$env:REMOTE_CONTROL_OUTBOX_RETENTION = '0'
# 恢复默认维护。
Remove-Item Env:REMOTE_CONTROL_OUTBOX_RETENTION -ErrorAction SilentlyContinue
# 临时启用导航诊断，正常使用不必开启。
$env:MC_CTRL_NAVIGATION_DEBUG = '1'
```

程序内部的 `ChatStore.retain_publication_outbox(pair_id=..., domain=...)` 默认只统计本候选窗，返回 `eligible/deleted/busy`；只有显式 `dry_run=False` 才删除。该方法是内部接口，不是建议对生产库手动执行的清理命令。

## 隔离查询基准

在源码目录执行：

```powershell
.venv/Scripts/python.exe scripts/benchmark_outbox.py
# 指定备份源与机器身份，仅经SQLite只读连接复制到临时库后测量。
.venv/Scripts/python.exe scripts/benchmark_outbox.py --source 'D:/backup/chat_history.db' --pair default
```

默认生成196167条及十倍规模的数据，各取20个暖样本。`--rows`须为正数、`--samples`至少1、`--growth`大于1。不存在的pair明确报错；outbox已清空但仍有facts的合法pair可测量。

输出区分全库 `counts` 与 `selected_pair_counts`（outbox/facts/pending），给出两个lane的实际查询计划和首轮、暖态median/p95。完整迁移 `upgrade_ms` 从临时旧schema状态执行真实 `initialize()`，包括新增索引、进度表及播种；它是总elapsed，不是单独写锁持有时间。首轮连接读取不等于清空操作系统缓存。合成数据、真实备份和不同轮次不能混合计算提升倍数。

## 构建输出与复制边界

`package_mc.ps1` 默认WorkPath为源码下 `.dev/build/pyinstaller`，临时staging也在所选WorkPath内；自定义WorkPath、DistPath、SpecPath保持有效。这里只改变未来输出位置，不自动清理旧树，也不设累计开发磁盘配额。

Python收集过滤依据包内目标路径和明确收集根内的相对源路径，不因checkout祖先名为 `test/tests` 误删资源。PowerShell最终复制同样排除开发、运行目录及APK、状态、日志和数据库文件，保留必要DLL、worker、NATS、声音、规则与随包技能；原用户history/home及外链节点守卫保持。

`-BuildOnly`只关闭最终自动启动，仍会更新指定DistPath下的mc。隔离验证必须使用独立DistPath/WorkPath；日常MC由用户退出/重启，不能把假构建复制测试当作真实安装验收。

## 当前验证边界

源码已验证有界扫描、发布水位/重放、worker维护交接、日志上限/故障恢复/短写、实际假构建复制输出、pair校验及Windows后代继承stdout的正常关闭路径。reader正在写盘时取消是否错过后续阻塞读仍是未复现的延期风险；正常关闭测试不能证明所有调度均安全。

安装版GUI、真实手机/隧道、24–72小时资源和月级稳定性仍需部署后另行验证；查询加速不等于已定位全部卡顿原因。
