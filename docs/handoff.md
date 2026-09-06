# 当前交接

## 任务目标（2026-09-06）

保证 Kimi Code 的多个聊天可并发运行：即使不同 Kimi session 都从相同的
`turn_id` 开始，事件也只能更新所属聊天，不能把新聊天留在“正在请求...”。同时保留
此前手机端 `kimi/*` 的专用 worker 分派与跨聊天隔离修复。

## 已完成

- `fb9261c76c08ef9e33badc3ad6e7c316aa44f200` 已以
  `(session_id, turn_id)` 路由 Kimi 事件；携带 session 的事件不会跨 session 按
  turn 回退，空正文完成事件不会把占位回复保存为成功。
- 缓存、完成清理和聊天状态也使用同一复合身份；后台聊天事件不改变前台焦点或无必要
  刷新列表。
- 电脑端已从该提交重新打包到 `D:\code\cx\mc`。`mc.exe` 的生成时间为
  `2026-09-06 17:55:14 +08:00`，SHA-256 为
  `EF39EDED353C1B6ED52A804E038732073A19B48485DFB8A08906077DDC4B25A1`；
  `mc_worker.exe` 的 SHA-256 为
  `9CC74FD73649D6D5A3CCC7D64E32CDA6C98CF962A6B4EC5984CEAB4DC2C12015`。
- 打包产物在隔离启动、关闭时正常；打包前后 `D:\code\cx\history` 指纹一致。

## 已验证

```powershell
python -m pytest tests/test_kimi_integration.py -q
# 26 passed

python -m pytest tests/test_kimi_ui_responsiveness_automation.py -q
# 5 passed

python -m pytest tests/test_kimi_server_client_unit.py -q
# 45 passed

python -m pytest tests/test_kimi_event_mapping_unit.py -q
# 60 passed

python -m py_compile main.py
git diff --check
```

同时已完成 PyInstaller 构建、隔离启动/关闭和历史目录指纹校验。构建中
`imageio_ffmpeg` 与 `pyaudio` 的未引用 optional hidden-import 警告不影响启动。

## 当前风险与下一步

- 当前分支 `fix/mobile-kimicode-routing` 没有配置 upstream，因此本地提交尚未能按
  收尾规则推送；不要擅自修改 remote 或 upstream。
- 定向测试不使用真实 Kimi CLI 服务。具备登录环境时，可执行
  `KIMI_LIVE_TEST=1 python -m pytest tests/test_kimi_live_smoke.py` 做真实链路冒烟。
- 2026-09-06 的全量非实时回归仍有既有跨领域失败，基线见
  [non-live-regression-baseline-2026-09-06.md](non-live-regression-baseline-2026-09-06.md)；
  本次未将其视为 Kimi 修复回归。

## 不应重复尝试

- 不要把 `turn_id` 当作跨 Kimi session 的全局唯一标识；有 session 时不得按 turn
  跨 session 回退。
- 不要用补配 `OPENROUTER_API_KEY` 掩盖 `kimi/*` 的路由错误；Kimi 使用专用 worker。
- 打包时保留同级的 `history` 数据，且不要单独启动 `mc_worker.exe`。
