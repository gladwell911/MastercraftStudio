# 当前交接

## 任务目标（2026-09-06）

修复手机端新建聊天选择 Kimi Code 后，电脑端把远端消息误送到 OpenRouter 并返回 `401 未授权` 的问题；同时保证活跃 Claude Code 客户端不会截获另一 `chat_id` 的远端消息，并形成可复现的 CR 与 QA 证据。

## 当前完成状态

- 分支：`fix/mobile-kimicode-routing`。
- `8dfd65fd7a8ec0cdc5e17723aa6b9317c1c959f7`：远端消息按规范化模型 ID 选择 Codex、Kimi、Claude Code 专用 worker；`kimi/main` 不再因来源为 `remote-ws` 回退到 OpenRouter。
- `5538a3906e9aae726b001c1e2771656113ed00af`：活跃 Claude Code 客户端与所属 `chat_id` 成对管理；只有同一聊天的输入可以续写该客户端，旧 worker 清理不会移除替换客户端。
- 正式代码审查完成：0 个需决策、0 个当前补丁、5 个既有 Claude 多会话限制延期。详情见 `_bmad-output/implementation-artifacts/deferred-work.md`。
- QA 已新增 `tests/test_mobile_kimi_cross_chat_e2e.py`，从 NATS `message` 命令路由覆盖桌面消息入口、聊天隔离与模型 worker 分派；同时修正 NATS 回退端口测试中硬编码的旧端口断言。
- BMAD 实现规格与 QA 总结位于：
  - `_bmad-output/implementation-artifacts/spec-mobile-kimicode-remote-401.md`
  - `_bmad-output/implementation-artifacts/spec-fix-active-claudecode-client-cross-chat-interception.md`
  - `_bmad-output/implementation-artifacts/tests/test-summary.md`

## 已验证内容

使用 Python 3.11 环境 `D:\code\sj\.build-envs\mc-py311`：

```powershell
D:\code\sj\.build-envs\mc-py311\Scripts\python.exe -m pytest tests/test_remote_model_dispatch.py -q
# 10 passed

D:\code\sj\.build-envs\mc-py311\Scripts\python.exe -m pytest tests/test_main_unit.py -q -k "claudecode and (new_chat or submit or worker)"
# 7 passed, 735 deselected

D:\code\sj\.build-envs\mc-py311\Scripts\python.exe -m pytest tests/test_mobile_kimi_cross_chat_e2e.py tests/test_remote_model_dispatch.py tests/test_remote_nats_unit.py tests/test_main_remote_nats_unit.py -q
# 52 passed

D:\code\sj\.build-envs\mc-py311\Scripts\python.exe -m py_compile main.py
git diff --check
```

## 当前风险与阻塞

- QA 是无凭据的进程内 E2E：经过 `RemoteNatsTransport` 命令路由、桌面消息入口和模型分派，但没有连接真实 Android 设备、NATS Server、Kimi 或 Claude 服务。
- 2026-09-06 的全量非实时套件存在跨领域历史失败，见 `docs/non-live-regression-baseline-2026-09-06.md`；本次仅以相关定向集作为提交闸门。
- 当前修复尚未重新打包到 `D:\code\cx\mc\mc.exe`。该路径中现有 EXE 是本次修复前的包，不能用来验收新行为。
- 当前分支没有配置 Git 上游。收尾流程可以本地提交，但按安全规则不能擅自新增上游或改远端，因此自动推送会被阻塞。
- Claude 同时跨多个聊天运行仍受单一活跃客户端槽限制；同聊天显式切换非 Claude 模型等延期项尚未处理。

## 下一步计划

1. 为 `fix/mobile-kimicode-routing` 明确配置期望的 GitHub 上游后推送本地提交。
2. 运行 `.\package_mc.ps1 -DistPath D:\code\cx`，确认新产物部署到 `D:\code\cx\mc\mc.exe`，并保留同目录 `mc_worker.exe`。
3. 用真实手机端新建 Kimi Code 聊天并发送消息，确认电脑端不再返回 OpenRouter 401；同时保持另一 Claude 聊天活跃，验证消息没有串聊。
4. 具备 Kimi CLI 登录环境时，用 `KIMI_LIVE_TEST=1` 运行 `pytest tests/test_kimi_live_smoke.py`。

## 不应重复尝试

- 不要通过补配 `OPENROUTER_API_KEY` 掩盖 `kimi/*` 的路由错误；Kimi Code 专用 worker 不依赖该 Key。
- 不要再用消息来源决定 CLI provider；远端和本地都应按模型 ID 分派。
- 不要把方法级或进程内 E2E 描述成真实手机全链路测试。
- 不要继续使用修复前打包的 `D:\code\cx\mc\mc.exe` 验收本次变更。
