---
title: '启动时迁移台式机旧 external 默认入口'
type: 'bugfix'
ticket: ''
created: '2026-11-02'
status: 'built'
route: 'oneshot'
route_source: 'auto'
review: 'quick'
review_source: 'auto'
lenses_ran: ['quick']
baseline_revision: '46326a5e9fe683ab037a55bb6258ff6a3069c7b0'
context:
  - 'D:/code/sj/.build-work/desktop-file-route-20261102/AGENTS.md'
  - 'D:/code/sj/.diagnostics/emulator-download-20261102/report.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** 日常台式机 external MC 持久配置仍为 `wss://rc.tingyou.cc/nats`，其原函数健康检查5秒超时，文件准备返回503；手机使用的新台式机入口认证检查通过。隔离模拟器已通过健康入口下完整UI下载、1MiB字节及SHA256验证。

**Approach:** 在现有启动配置加载路径中，仅把default身份、external模式、没有显式DOMAIN或本地HOST/PORT覆盖的旧默认地址迁移为 `wss://mc-desktop-test.tingyou.cc:8443/nats`。保留原token和pair，复用现有原子持久写入，并使app_state显示字段进入原保存流程。自定义地址、laptop、cloudflared、环境显式地址不迁移。不修改HTTP健康检查设计、不部署或重启日常MC、不写日常配置、不修改RC。用户已授权先模拟器测试后源码修复，并明确使用隔离工作树。

</frozen-after-approval>

## Implementation Notes

- 小范围启动配置修复，预计业务代码小于30行，使用oneshot。工作树 `D:/code/sj/.build-work/desktop-file-route-20261102`，分支 `fix/desktop-file-route-20261102`；原树未提交文档保留。
- Given旧default配置和external模式，when启动，then配置原子迁移且原凭据保留、运行消息URL及文件base同新origin并保留8443。
- Given显式覆盖、其他来源或自定义域，when加载，then不迁移。Given再次启动，then不重复写机器配置。
- 现有load_machine_config负责归属、规范化与原子写入；main初始化负责同步内存与app_state保存。只复用这些路径。

## Verification

- 使用原仓库 `.venv/Scripts/python.exe`，在本隔离目录运行配置单测及真实ChatFrame启动/文件base定向回归；先red再green。
- 复用此前隔离模拟器最终全绿证据：同一生产下载链在健康入口下完整1MiB及SHA256通过。本次只改启动迁移，新增真实ChatFrame测试校验实际加载后的消息URL、文件origin及app_state，下载实现未变，不重复无改动UI链。不声称原97MB公网实机已修好。

### 验证记录

- 新增10例先red：4 failed / 6 passed；旧地址迁移与状态保存缺口按预期失败。
- 修复后身份/迁移初选17 passed。扩展选择发现开发机继承external环境，旧cloudflared默认测试受环境影响；conftest显式清除两种隧道模式覆盖，专门external测试自己设置。
- 扩展选择首次40 passed / 1 failed：既有并发原子替换用例遇WinError5；同一选择无代码修改重跑41 passed / 1050 deselected（4.82s）。该单次失败记录保留，未断言基线稳定通过或并发写入已修复。
- 真实ChatFrame将app_state写入fixture临时目录，断言新domain与原token；旧持久域在现有原子机器配置写入后再次加载不改mtime。
- 日常进程、配置、凭据和实体手机未操作；原mc树的未提交文档未改。未打包、部署、推送。

## Review Triage Log

- quick独立审查1项：medium / patch。环境变量存在但HOST/PORT为空白时，主运行配置忽略该值，迁移也应忽略。已改为非空白判断，新增REMOTE_CONTROL及CLAUDECODE两类别名的HOST/PORT共4例。最终同一完整定向选择45 passed / 1050 deselected（4.91s）。无defer项。

### 新Dog四视角审查后续

- P1 / medium / patch：空白DOMAIN在setting按键存在取值，导致旧域迁移失效；两个别名已用隔离fixture复现。由bmad-build-auto计划plan-fix-blank-domain-migration.md处理。
- D1 token-only app_state保存、D2 Windows并发replace WinError5为既有行为，留在审查报告，未扩大本次迁移修复。
