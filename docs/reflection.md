# 本轮反思

| 修正内容 | 错误归因 | 下次指令建议 |
|---|---|---|
| 只修源码、未立即验证目标 EXE。 | 判断逻辑有问题 | 打包桌面问题先检查目标包的真实入口和子进程命令。 |
| 将 GUI 包复用作 JSONL worker。 | 判断逻辑有问题 | GUI 和标准流 worker 必须使用独立 console EXE，并做真实 IPC 冒烟。 |
| 未考虑 Windows 保留端口与本地代码页。 | 信息不足 | 端口服务需可验证回退；跨进程 JSONL 两端必须显式 UTF-8。 |
## 2026-09-05 Review correction

| Correction | Root cause | Reusable guidance |
|---|---|---|
| A selected historical chat could receive updates intended for the hidden active chat, while execution refreshes could reintroduce stale physical rows. | View selection and persisted/active state were conflated; reconciliation trusted stale incremental data. | Route mutations through the selected history identity, and rebuild visible execution state from one canonical ordered model before repainting. |
