# 可复用经验

- OneDrive 数据：源码与打包版的笔记、常用命令路径不同。打包版须在初始化前验证目标存在、SQLite 完整且表结构属于已知笔记库。在线 SQLite 备份生成的是定时快照；先关闭备份连接，再校验并放置文件。跨机不并发运行 MC，换机先退出并等同步；不能直接用另一台旧库覆盖目标。
- wx 可访问性：可见状态未变时不重绘列表、不移动焦点。后台事件先合并，再按 chat、turn、session 与 generation 复核归属；真实定时器测试负责完整清理。
- 远程事件：provider 按规范化 model id 分派；可继续接收输入的 CLI 客户端绑定 chat_id。Kimi 文本来自 REST /messages，事件流用于活动触发；最终回答以持久化 done turn 为事实来源。
- 终答补发：保存 done 回合后才投递 final/history；短暂失败保留待发项并重试。Codex 事件仅绑定唯一可确认的 turn，不能用最后一轮兜底；无意义的 Not Loaded 占位可过滤，实际错误必须保留。
- 执行投影：桌面 canonical 步骤与远程快照同源，分页绑定 owner 与 revision。全量读取、投影、哈希和快照放 worker；UI 只读有界状态。流式事件覆盖乱序、缺口、重放、清空和重启。
- 验证边界：Local、只读 Live、真实模型、模拟器和实体机分别记录；宽套件既有失败按精确用例复核，不用定向通过替代全量结论。

案例、协议细节和其余经验见 [归档原文](archive/entry-context-2026-09-29/experience.md)。
- Startup callbacks must verify the original turn/request identity on the UI thread; chat_id/turn_idx can be reused after clear. Codex start generation stays distinct from context generation after native thread binding. A fast answer must follow worker ownership ACK in delivery order; bounded startup overflow fails explicitly, and late final events cannot revive failed requests.


Startup buffering must only defer newly unacknowledged events. Preserve prior native owners through new-request failure, and carry source client identity through every asynchronous boundary until actual UI application.
