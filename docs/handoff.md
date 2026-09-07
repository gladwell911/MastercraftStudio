# 当前交接

## 快照（2026-09-07）

当前分支为 `fix/mobile-kimicode-routing`。最新本地提交
`38968450ff1c8d6e354ecf4d95652b5a16ca4f52`（`完善 Kimi 执行步骤中文显示`）已完成
Kimi Code 的 F1 执行过程中文化与流式步骤归并。本分支没有 Git upstream；按收尾规则，
不要修改 remote 或 upstream，也不要以裸 `git push` 推送。

## 已完成

- Kimi 原始事件由 `kimi_server_client.py` 保留来源、代理、序列/offset 与文本片段边界；
  重放片段会去重，来源不同的流不会混合。
- `main.py` 根据 Kimi 的结构化事件生成“正在分析问题”“正在搜索内容”“正在读取文件”
  “正在修改文件”“正在执行命令/测试”“正在整理回答”等中文 F1 主要步骤。
- 英文 `thinking.delta`、assistant 增量和工具流原文不再直接作为 F1 列表标题；状态、
  usage 与静默通知不会切断一段流式文本。原始诊断仍保留在详情数据中。
- 已保持多聊天事件隔离、后台批量交付、无可见变化不重绘、焦点和当前选择不被后台事件
  改动的无障碍约束。
- 用户入口说明和文档索引已同步：`README.txt` 说明 F1 的中文主要步骤，
  `docs/README.md` 链接验收规格。

## 已验证

```powershell
py -3.11 -m pytest tests/test_kimi_event_mapping_unit.py tests/test_kimi_integration.py -q
# 91 passed

py -3.11 -m pytest tests/test_kimi_ui_responsiveness_automation.py -q
# 6 passed

py -3.11 -m pytest tests/test_kimi_server_client_unit.py tests/test_main_unit.py -q -k "kimi or execution"
# 141 passed, 15 failed, 636 deselected

py -3.11 -m compileall -q main.py kimi_server_client.py tests/test_kimi_event_mapping_unit.py tests/test_kimi_integration.py tests/test_kimi_server_client_unit.py tests/test_kimi_ui_responsiveness_automation.py tests/test_main_unit.py
git diff --check
```

第三条命令的 15 项失败与
[`non-live-regression-baseline-2026-09-06.md`](./non-live-regression-baseline-2026-09-06.md)
记录的通用执行列表分页、导航、增量追加失败集合一致；本次新增的 Kimi 断言均通过，
不应把这批基线失败当作本功能回归。

## 当前风险与下一步

- 未识别的未来 Kimi 工具类别会退化显示为“正在处理任务”；若 Kimi 协议新增常用类别，
  应补充中文摘要映射和 fixture 回归。
- 当前验证未连接真实已登录的 Kimi CLI 服务。具备环境时可运行
  `KIMI_LIVE_TEST=1 py -3.11 -m pytest tests/test_kimi_live_smoke.py` 做真实链路冒烟。
- 若要处理 15 项通用执行列表基线，应另立任务，先确认其现行无障碍与分页产品契约，
  不要为旧断言回退本次的 Kimi 流式归并或焦点保护。

## 不应重复尝试

- 不要按“是否含英文字母”过滤 F1 行，也不要依赖中文提示词来控制模型内部 thinking；
  应继续以结构化协议事件生成稳定摘要。
- 不要把 status、usage 或静默 session 通知当作文本 flush 边界，也不要让 thinking、
  assistant 与 tool progress 共用一个流缓冲键。
- 不要把 `turn_id` 当作跨 Kimi session 的全局唯一标识；携带 session 时不得跨 session
  回退。
