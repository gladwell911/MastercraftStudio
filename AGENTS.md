# 项目指令

每次开始新的 Codex 项目会话，先读取 docs/handoff.md、docs/experience.md 和 docs/reflection.md；再按任务读取 docs/README.md。历史快照与冻结基线只证明对应版本。

- 本项目是面向读屏用户的 wxPython 桌面应用。没有可见状态变化时，不在后台轮询中触发 UI 重绘、选中变化或状态写入；保持键盘焦点稳定。
- 重新打包 MC 时默认不备份旧程序包，不创建 `mc-before-repack-*` 等旧包副本；只有用户明确要求时才备份。使用 `package_mc.ps1` 先在临时目录完成构建和必要校验，再更新 `D:\code\cx\mc`，保留现有笔记、聊天历史及其他运行数据。最终产物校验通过后自动启动一次；清理 reparse 只操作链接节点，不遍历外链目标。用户要求只修改/测试时不得执行真实打包或启动。
- Codex 私有 home 通过整目录链接复用全局 skills/plugins，新任务刷新原生清单，活动 turn 的 steer 不重启。不得沿链接改写全局技能文件或退回长期副本；来源与验证边界见 docs/handoff.md。
- UI 变更至少运行相关 tests/test_*ui_automation.py 和受影响模型流程测试。wx GUI 套件串行运行；真实定时器从 frame 构造到销毁全程清理。
- CLI 后续输入、异步状态及结果必须按 chat、turn、session、account 和 request generation 归属；按规范化 model id 分派 provider。执行页保持 owner、revision 与有界前台读取，重工作放后台。
- Kimi REST 回答、思考、分页与旧 owner 恢复共用结构化 prompt 边界判定；仅明确标记的 injection 可跳过。metadata.origin 的优先级与未知值保守规则见 docs/README.md，不能按消息正文识别注入。
- 源码笔记路径为 D:\code\note\notes.db；打包版需要现存且经完整性校验的个人 OneDrive\code\data\sj\notes.db，缺失时在初始化前停止。测试须 monkeypatch resolve_notes_data_dir()。
- 切包门禁：2026-09-29 的 OneDrive 数据库只是一次快照，早期安装包曾写本地源库。新包启用前核对实际运行包的数据路径，补齐之后的改动并安全替换或合并；跨机只运行一台 MC，换机前退出并等同步。当前步骤见 docs/handoff.md。

原指令细节见 [归档](docs/archive/entry-context-2026-09-29/AGENTS.md)。
