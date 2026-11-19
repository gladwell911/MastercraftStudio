# Kimi 功能修补的独立审查 · 2026-11-19

范围为 6c5d2cb..904cda1，6 个文件，+1772/-267。按项目要求依次完成 blind-hunter、edge-case-hunter、verification-gap、intent-alignment 四个独立透镜。full 模式使用提交时的实施计划及其两个 context。发现 8 个可复现的产品问题和 2 个测试缺口，0 项需要决策，1 项沿用既有待证记录，5 项意见不另行修补。

## 修补清单

| 编号 | 等级、位置及后果 | 最小修补与回归 |
|---|---|---|
| R1 | 高 · main.py:11712、11790、10770。普通 /clear 不替换原 turn。首轮 POST 回来后旧 owner 可重新登记，排队的启动回调也可写回旧 session。已观察正常回包恢复旧 session，及未知回包在最后检查后、登记前执行 /clear，下一条实际提交仍命中同一个 session-fake-1。 | 以该聊天提交锁的身份标记退休，把回包登记、活动 owner 发布和 UI 回调均绑定有效身份。保留普通 /clear 历史；旧清理不能清除后来的新 owner。回归 test_slash_clear_post、test_clear_unknown_window_next_send（并覆盖延后 UI 回调）。 |
| R2 | 中 · main.py:1493。Kimi 单选使用 RA_SPECIFY_ROWS 且 majorDimension=1。六个带说明的选项测得一行六列、所需 1440px、仅有 588px 可视宽度，父控件只纵向滚动。 | 单列排列，保留选择与选项 ID 映射。回归 test_radio_layout，检查末项正常宽度可见。 |
| R3 | 中 · main.py:16622、11447。重试窗显示期间到期或 /clear 退休时只设 ended，未结算错误。实测菜单不再有可操作项，聊天仍提示可重试。 | 对本次结束的交互清理其错误，保留别的交互的错误；无需到期定时器。回归 test_expiry_during_retry、test_clear_leaves_stale_error。 |
| R4 | 中 · main.py:16511、16678。HTTP 成功先设 ended 后，WS ended 提前返回而永不执行裁剪。连续 513 个请求保留全部 513 个含完整 params 的记录，超出 512 上限。 | 在实际终态转换处执行同一有界退休逻辑，保留活跃请求及近期重放去重。回归 test_http_first_retention。 |
| R5 | 中 · main.py:16565。两项同时可重试，先登记项一直 uncertain，三次菜单操作全部查询 first；第二项始终不可达。 | 保留当前菜单；使重复操作能公平到达其他可操作身份，仍按具体 session、interaction 查询或回答。回归 test_first_error_starves_another。 |
| R6 | 中 · main.py:16661、kimi_server_client.py:804。手动只读重查时客户端启动的 OS 错误未处理，真实 Popen 找不到目标时报 FileNotFoundError，交互永久留在 rechecking，重试菜单没有该项。 | 对客户端启动/查询的明确系统异常恢复 uncertain 并反馈原聊天，不发送答案。回归 test_recheck_start_failure。 |
| R7 | 中 · main.py:16710、22799、4523。切到 B 后 A 收到 uncertain 错误，归档 SQLite 会保存“可从应用菜单重试”；请求记录仅在内存，重载后即使已结束也没有对应操作。实际归档 SQLite 回读保留了该字符串，同一探针对活动聊天未复现。 | 加载/显示时移除没有可操作请求的旧提示，保留仍有归属的错误及后续 pending 重放。回归 test_restart_archived_error，增加重载后菜单/显示断言。 |
| R8 | 中 · main.py:16695。后台错误反馈已排队、WS ended 抢先处理，随后错误回调仍发布 action_required；过期检查只挡住本地状态栏。记录器捕获一条 session-fake-1/i/error。 | 推送前核对请求仍处于对应错误状态。回归 test_remote_attention_after_end，同样检查真正未决请求仍提醒。 |
| R9 | 中（验证缺口）· tests/test_kimi_integration.py:3922。审批夹具有 Shell、Run the local checks、pytest -q，测试检查按钮和 POST，删除展示命令仍会通过。 | 在现有 test_v2_wire_interaction_reaches_native_controls_and_exact_http 审批分支读取原生显示正文，断言三项可见。 |
| R10 | 中（验证缺口）· tests/test_kimi_integration.py:4058。中断被拒仅直接调用客户端；若聊天 worker 错报“已受理”，当前测试仍通过。 | 扩展 test_v2_stop_exact_prompt_http_and_cancelled_terminal，从聊天实际提交 /stop 让该 prompt 返回 aborted:false，检查失败提示、原提问仍 pending 且能自然完成。 |

## 复核依据

隔离项目 wx frame、临时 ChatStore/笔记和假服务。逐个执行可控交错，报告修订前合并复跑结果为 10 条预期失败、2 条反例通过。位置和回归名称对应临时探针 C:/Users/gladwell/AppData/Local/Temp/mc-fresh-7814af1/test_probe.py，完整原始结果同目录 results.txt。R8 捕获通知提交入口，未连接远程设备。R6 使用临时不存在的 exe 验证真实 OS 启动失败。未打包、部署或使用日常认证。已读原最终脱敏输出：隔离 2.5.3 客户端问答/取消 2 passed、源码 wx 问答/取消 1 passed。真实云端 pending 和物理键盘/读屏尚未验收。三项既有 integration 失败、两项旧 native V1 fixture 超时不计入新发现。

## 透镜逐项裁决

B、E、G、I 分别指上列四个透镜。以下按收集顺序裁决，再按上表合并。D 为本轮协调审查的补充确定性检查。

| 序号 | 来源 | 裁决与证据 |
|---|---|---|
| 1 | B1 | maybe-false / defer，kimi_server_client.py:936。缺 ID 分支明确；尚无固定 2.5.3 接纳且返回缺 ID 的应答与 transcript 对照。沿用已知 D2。 |
| 2 | B2 | false。固定版本成功审批 outcome 由请求 decision 产生，抢先结束会返回错误。 |
| 3 | B3 | false。正常首轮 POST 内注入 pending，活动 owner 可映射聊天；回包落地后第二次展示检查精确找到该请求（test_early_interaction）。 |
| 4 | B4 | medium，R3。已过期后返回仍残留错误提示。 |
| 5 | B5 | medium，R4。513 条退休超过上限。 |
| 6 | B6 | medium，R5。三次只取第一项。 |
| 7 | B7 | medium，R6。已核实可达的系统启动失败，无需为任意假设的坏 schema 加防御。 |
| 8 | B8 | medium，R7。归档与活动聊天分别核验。 |
| 9 | B9 | medium，R2。原生控件几何越界。 |
| 10 | B10 | low / reject。源码 wx 取消可由 REST 先结算，这是有效生产路径；独立 live abort 已强制断言 WS cancelled。同一 wx 用例再强制 WS 赢得去重竞争会错误拒绝正常时序。 |
| 11 | E1 | high，R1。已让用户下一条 POST 落回旧 session。 |
| 12 | E2 | medium，R5。与 B6 相同。 |
| 13 | E3 | medium，R3。与 B4 相同。 |
| 14 | E4 | medium，R8。远程提醒入口确实被调用。 |
| 15 | E5 | medium，R4。与 B5 相同。 |
| 16 | E6 | low / reject。与 B10 同一测试边界及现有独立 WS 证据。 |
| 17 | G1 | medium，R9。现有 V2 原生测试未读取审批正文。 |
| 18 | G2 | medium，R10。拒绝仅断言客户端抛错。 |
| 19 | G3 | medium，R4。与 B5 相同。 |
| 20 | I | false（作为新增缺陷）。原实施范围允许固定协议帧、线程及持久化模拟；真实键盘、云端交互及进程重启证据有边界，未许诺完成对应整体验收。 |
| 21 | D1 | high，R1。正常回包和普通 /clear 也复现。 |
| 22 | D2 | medium，R3。普通 /clear 后指向不可操作菜单的错误仍在。 |
