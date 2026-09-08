# 当前交接

## 快照（2026-09-08）

分支 `fix/mobile-kimicode-routing`，本阶段代码提交 `80c4606b808a5d5915b2367f7c1f3cf41d6abb50`。目标是修复桌面执行列表的阻塞、分页、选择和异步恢复，保留历史仅查看、F1 问答上下文、Kimi 中文摘要及权威完成/多聊天隔离。完整诊断、15 项旧失败逐项处理、审查和 AC1–AC10 证据见[执行规格](../_bmad-output/implementation-artifacts/spec-fix-execution-ui-blockers.md)。

## 已完成

- 执行页从权威聊天状态生成一次投影，原生行增量对齐并修复物理旧尾项；同状态零控件写入，正常批次最多一次同步。选择跟随内容身份，而非固定下标；仅显式 F1 定位最新项。
- 初始 100 内容行包含合成问题/最终回答，“更多”额外计数。历史读取不全量加载执行记录；前台最多两次有限页查询，余下隐藏历史扫描移到后台，游标续页不重复 COUNT。存储位置、新记录 UID 与有限页 legacy 匹配保留合法重复记录和来源切换选择，无 schema 迁移。
- 跨聊天/轮次等待或失败时清除旧详情并显示 loading，标签、meta 和打开/复制入口同归属。异步 F1 意图随请求完成，用户已离开则不抢焦点；切换、清空、页大小改变及关闭拒收旧结果。
- 读取/投影/原生写失败保留 dirty 并自动重试，不打断 Codex/Kimi 队列及持久化。quiet 内后台不写列表；销毁后的 idle 回调清引用后直接返回。
- Kimi 既有 `(chat_id, session_id, prompt_id)` owner、流完整性、权威最终回答、失败不播放完成音等行为保持；本阶段没有外部协议或手机端改动。

## 已验证及复现命令

以下结果来自 **2026-09-08 上一轮代码最终独立验收**，不是本次文档收尾重跑。使用临时数据与 fake provider，wx 套件串行执行。

```powershell
py -3.11 -m pytest tests/test_main_unit.py -q -k "kimi or execution or switch_current_chat" --tb=short
# 148 passed, 631 deselected；1项既有wx弃用warning
py -3.11 -m pytest tests/test_main_unit.py -q -k "idle_history or idle_ui" --tb=short
# 6 passed, 773 deselected
py -3.11 -m pytest tests/test_codex_ui_responsiveness_automation.py -q --tb=short
# 20 passed
py -3.11 -m pytest tests/test_kimi_event_mapping_unit.py tests/test_kimi_server_client_unit.py tests/test_kimi_integration.py tests/test_kimi_ui_responsiveness_automation.py tests/test_mobile_kimi_cross_chat_e2e.py tests/test_remote_model_dispatch.py tests/test_main_remote_nats_unit.py -q --tb=short
# 244 passed
py -3.11 -m pytest tests/test_listbox_model_unit.py tests/test_chat_store_unit.py tests/test_file_extraction_ui_unit.py tests/test_common_commands_ui_automation.py -q --tb=short
# 49 passed
py -3.11 -m compileall -q main.py listbox_model.py chat_store.py tests/test_main_unit.py tests/test_codex_ui_responsiveness_automation.py tests/test_chat_store_unit.py tests/test_listbox_model_unit.py
git diff --check
```

去重共 **467 项**；Codex GUI 子筛选 8 项已含在完整 20 项内，不重复计数。真实 GUI 的 10 轮/40 事件为 10 次同步，导航中位 15.01ms、最大 55.68ms；第 3 次后台读取阻塞期间 Tab 为 5.07ms，前台 2 页/总 5 页。原 0.5 秒门槛未放宽。

[2026-09-06 冻结基线](./non-live-regression-baseline-2026-09-06.md) 的 `120 failed, 1421 passed` 是历史记录，原始数字不改。本阶段原 15 项 execution red 已解决，但没有重跑全仓其余领域，不能据此计算当前剩余失败数或声称全仓全绿。

本次 2026-09-08 文档收尾只核验六份文档的本地链接、命令文件路径及 `git diff --check`，均通过；上述 pytest 命令改用 `--collect-only` 后分别确认主筛选 148、idle 筛选 6、其余文件合计 313 项可收集。没有启动 GUI、真实服务或再次执行这 467 项测试。

## 风险、阻塞与下一步

1. 优先安排人工读屏验收：跨聊天 loading→内容替换时的播报归属/顺序仍未听测。真实付费 provider 未执行；Kimi live smoke 需要已登录 CLI 和显式 `KIMI_LIVE_TEST=1`，不可把默认跳过算作通过。
2. 执行规格保留四项 deferred：初始同步 SQLite 单次慢读；历史跨轮平面列表只有一个最新有效轮上下文；quiet 轻量通知按事件数累积；共享模型的全笔记领域未完整验收。后续分别立项，不扩充本轮完成声明。正在执行的 SQLite 调用在返回后退出，不强杀线程。
3. 如继续治理全仓旧失败，先按冻结基线分领域复现，保留当前上下文、身份、导航及 quiet 契约；另行记录实际全量结果。
4. 本分支未配置 Git upstream。`origin` 唯一 push URL 为 `https://github.com/gladwell911/MastercraftStudio.git`，但不能据此猜测目标分支。文档收尾可本地提交；推送阻塞，需维护者先明确配置上游。没有合并 main、部署或修改真实用户数据。

## 不应重复尝试

- 不用旧固定行号或控件当前条数代替内容身份断言；不把 quiet 通知当作独立业务数据重放。
- 不将“每页有限”当作 GUI 总耗时有限，也不以“缓存完成”要求 quiet 内立即刷新。
- 不靠增加 sleep、放宽门槛、跳过测试或关闭正常 drain/保存来获得绿色 GUI；完整事件循环应验证发键时仍有 pending。
