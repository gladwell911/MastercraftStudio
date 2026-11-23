---
title: 'MC 使用本机 Kimi CLI'
type: 'feature'
created: '2026-11-23'
status: 'done'
baseline_revision: '70759dc04c9603d366f82306174113127adad834'
review_loop_iteration: 0
followup_review_recommended: false
context: []
warnings: []
deferred: []
---
<intent-contract>

## 意图

使用本机 Kimi CLI，解除 MC 打包对 2.5.3 二进制的依赖，并保留已实现的 Codex OneDrive 配置。

## 边界

使用显式绝对路径 KIMI_BIN 或当前用户官方安装路径，只在 Kimi 功能中启动，禁用子进程自动更新。仅放行有兼容证据的版本，保留握手、订阅及恢复门禁。采用隔离目录验证，不覆写真实会话、发布或替换日常安装。

| 场景 | 输入 | 结果 |
|---|---|---|
| 默认使用 | 本机已验证的 Kimi CLI | 启动本地进程并通过现有协议门禁 |
| 自定义路径 | 有效绝对 KIMI_BIN | 优先使用该文件 |
| 安装缺失或版本未验证 | 启动 Kimi | 清晰提示版本或路径，MC 和 Codex 正常 |
| 没有固定二进制 | 正常打包 | 完成构建，产物不捆绑 Kimi |

</intent-contract>

## Code Map

- [原计划](plan-use-local-kimi-cli.md) 记录真实 2.6.3、历史兼容和回滚验收。
- kimi_server_client.py、kimi_runtime.py 负责可执行路径、探针与启动。
- zgwd.spec、package_mc.ps1 负责收集与打包校验。
- codex_client.py 负责 OneDrive 代理变量注入。

## Tasks & Acceptance

- kimi_runtime.py、kimi_server_client.py 实现本机查找和按实际兼容证据放行。
- zgwd.spec、package_mc.ps1 移除捆绑与固定二进制门禁。
- tests/test_packaging_specs.py 及相应 Kimi 测试覆盖缺失、覆盖、版本门禁与启动。docs/kimi-runtime.md 更新对应运维说明。
- Given 本机 2.6.3，when 在隔离环境问答、取消、重连和读取旧会话，then 确认协议与状态归属符合计划，证据不足时记录限制并保留实际门禁。
- Given 缺少包内 Kimi 二进制，when 正常构建，then 隔离产物不含该文件，实际 worker 启动 Codex 时仍读 OneDrive 代理配置。

## Spec Change Log

## Review Triage Log

### Review Findings

- [x] [Review][Patch] 2.6.3 审批 POST 结果不明时无法确认仍待审批的请求，菜单无法恢复可重试状态 [kimi_server_client.py:509]
- [x] [Review][Patch] 2.6.3 已结束的审批回包可把取消或冲突结果报告成用户操作成功 [kimi_server_client.py:504]
- [x] [Review][Patch] 2.6.3 `item.updated` 的 `item_id` 和 `set` 未应用，`item.removed` 未处理，执行过程可能显示已被更新或移除的内容 [kimi_server_api.py:167]
- [x] [Review][Patch] 2.6.3 终态 REST 在 WebSocket reader 串行阻塞事件接收和心跳，补查失败还发出虚假传输恢复事件 [kimi_server_api.py:247]
- [x] [Review][Patch] 原始 ordinal 已前进时，纯非 agent 页仍会中断旧 prompt 恢复 [kimi_server_v2.py:379]
- [x] [Review][Patch] Codex OneDrive 代理缺少实际子进程环境的自动回归，已有冻结 worker 手工证据 [codex_client.py:994]
- [x] [Review][Defer] 2.6.3 在 ACK 前丢弃同代用量帧 [kimi_server_api.py:154] — deferred: 当前真实协议记录均先收到 ACK，再收到初始 usage，需要观察到逆序或确认协议允许逆序。
- [x] [Review][Defer] 重订阅快照只缓存运行中 turn，未直接投影执行文本 [kimi_server_api.py:159] — deferred: 复现未完成，需验证已有恢复任务能否补齐审批等待期间的执行正文。
- [x] [Review][Defer] 2.5.3 旧会话迁移、降级读取、台式机与实体读屏验收 [docs/kimi-runtime.md:13] — deferred: 需要真实历史样本、第二台电脑和实体读屏，隔离冻包只覆盖笔记本一轮发送与收取。

#### Rejected

- `false` · `turn.started` 丢失只在 turn 内的 opener。实测启动帧不带 item，随后独立 `item.ended` 发送 opener。随附客户端也将新 turn 的 `items` 初始化为空。
- `false` · 终态 REST 仅检查第一页就永久遗漏旧 prompt。第一页未匹配会触发既有有界、按精确 prompt 的分页补查，真实缺陷是阻塞 reader 和误用恢复事件。
- `low` · 隐藏 tool frame 和隐藏 opener 的组合。当前证据未展示 2.6.3 按此方式标记这两种项目；tool frame 也是防止把工具所在步骤当成最终回答的结构信号，不能仅凭未知可见性移除它。
- 规格旧验收段落和后续证据冲突属于文档状态更新。BMAD Step 3 从补丁发现中排除需要修改被审规格条目的建议。

## Verification

执行 Kimi/Codex 定向回归与隔离构建，报告模拟、真实运行时和生产验收各自的结果。

## 初次运行记录（历史）

- 状态为 blocked。2.6.3 的真实问答、取消、重连与旧会话验收被协议变化阻塞，未进入 BMad 最终审查与提交。
- 已实现本机路径发现和兼容门禁，移除打包对固定 Kimi 二进制的依赖。当前仍仅放行有历史兼容证据的 2.5.3 构建。
- 隔离 2.6.3 服务可启动，WebSocket 认证通过。旧握手收不到 server_hello，随附客户端改用 subscribe 和 event.*，需先完成协议适配。
- 标准 package_mc.ps1 -BuildOnly 在独立 DistPath 与 WorkPath 退出 0，产物包含两个 MC 入口且无 Kimi 可执行文件。冻结 worker 用 Codex 替身验证了两种机器身份的 OneDrive 覆盖和缺配置行为。
- 定向 Kimi、Codex、打包测试共 270 项通过。Engineer 独立复验了三项流式失败与基线一致。日常安装和数据未替换。本批迁移改动保留在工作树供继续开发。

## 当前状态
2026-11-23 已接入并验收 2.6.3。六项审查发现均已修复，复验见 [审查修复规格](spec-kimi-server-review-fixes.md) 和 [实测记录](result-kimi-acp-vs-server-20261123.md)。