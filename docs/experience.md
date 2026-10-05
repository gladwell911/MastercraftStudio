# 可复用经验

- 回答时间：问题与权威答案分别记录，完成重放不刷新。验证必须经过已有流式回答行的真实 provider 完成和 quiet/deadline；字段已写或重载后正确不能代替实时表面检查。投影重算保留选中和焦点，pending delta 不全量重建。
- Codex 命令审批：主动策略选择作用于后续启动/恢复，实际 request 和 reply 均复核 owner/generation/client；关闭按拒绝处理。单独修改启动策略不能代替完整的交互回复链。
- 详情纯文本投影需保留链接目标、显式空行、列表层级和表格列，HTML 属性解析须允许无值或非法序号。用真实 Markdown 和 HTML 两类输入核对内容；窗口转换放在销毁 finally 的保护范围内。换行边界只维护末尾状态，避免每个段落拼接累计全文。
- 上下文专用请求不能吞掉完整信息刷新：合并一次完整刷新意图，请求完成后重新核对 dialog/owner，再补发。分别覆盖成功、失败、关闭、owner 切换；IPC 标志通过实际 client 序列化到 worker 消费验证，不能只在测试里手造字段。

- 聊天活动：发送受理和首次成功权威回复才更新活动时间；失败终态与重放不更新，保留置顶组。直接更新历史单行而非只排 history dirty，保持身份和焦点；用更晚的竞争聊天与严格时间增加区分发送和收到回复的两次更新。

- OneDrive 数据：源码与打包版的笔记、常用命令路径不同。打包版须在初始化前验证目标存在、SQLite 完整且表结构属于已知笔记库。在线 SQLite 备份生成的是定时快照；先关闭备份连接，再校验并放置文件。跨机不并发运行 MC，换机先退出并等同步；不能直接用另一台旧库覆盖目标。
- wx 可访问性：可见状态未变时不重绘列表、不移动焦点。后台事件先合并，再按 chat、turn、session 与 generation 复核归属；真实定时器测试负责完整清理。
- 远程事件：provider 按规范化 model id 分派；可继续接收输入的 CLI 客户端绑定 chat_id。Kimi 文本来自 REST /messages，事件流用于活动触发；最终回答以持久化 done turn 为事实来源。
- 终答补发：保存 done 回合后才投递 final/history；短暂失败保留待发项并重试。Codex 事件仅绑定唯一可确认的 turn，不能用最后一轮兜底；纯 Not Loaded / notLoaded 字符串及子代理占位可过滤；混合结果、失败、非零退出及有用描述必须保留。分别检查事件入口与历史投影，不能只匹配整行。
- 执行投影：隐藏页在 worker 准备有界缓存，重复刷新复用在途任务；F1 显示切换不是数据失效，不取消已准备缓存。读取缓存先于历史水合，隐藏完成不重绘或抢焦点；用慢存储、失败重试及旧 owner 回调验证。canonical 步骤与远程快照同源，分页绑定 owner/revision；全量投影、哈希和快照放 worker。
- Kimi 恢复预算：等待后再核对 deadline，多 owner 共享预算须覆盖后续 owner 一进入就过期。错误优先保留原始及最后真实异常，无真实错误时才用超时兜底；未执行 client.start 不能称作子进程启动失败。
- 验证边界：Local、只读 Live、真实模型、模拟器和实体机分别记录；宽套件既有失败按精确用例复核，不用定向通过替代全量结论。

案例、协议细节和其余经验见 [归档原文](archive/entry-context-2026-09-29/experience.md)。
- Startup callbacks must verify the original turn/request identity on the UI thread; chat_id/turn_idx can be reused after clear. Codex start generation stays distinct from context generation after native thread binding. A fast answer must follow worker ownership ACK in delivery order; bounded startup overflow fails explicitly, and late final events cannot revive failed requests.


Startup buffering must only defer newly unacknowledged events. Preserve prior native owners through new-request failure, and carry source client identity through every asynchronous boundary until actual UI application.

## 2026-10-01 验证方法

- 先核对交付树 HEAD，不用主树或旧日志代替；锁屏可见像素、对象字段、跨端传输分别记录。
- `Raise()` 和 wx 内部焦点不足以证明 Windows 前台。使用 `tests/owned_window_qa.py` 真实点击测试窗口标题栏，严格检查 HWND 后保留原键盘和焦点断言；finally 恢复置顶状态和鼠标，不添加全局键盘钩子。

## 2026-10-04 当前补充

- 执行投影：写入维护 visible 位，初始化一次原子回填，部分索引获取可见尾部。LIMIT 不证明扫描工作有界；用大量隐藏后缀与 SQLite 指令数验证。F1 展示当前 owner/view/turn/revision 的真实尾页，dirty 只使扫描新鲜度失效，worker 补齐；冷打开通过可视索引获取真实内容。
- Kimi 自有 REST Session 在共享前设置 trust_env=False，WS 绕过本机代理；GET 最多一次剩余预算内恢复，POST 结果不明不重发。provider 的 10054 仍是失败，不得根据错误文字假称恢复。
- 桌面验证区分 HWND WM_CHAR/BM_CLICK、wx F1 事件、窗口键消息与物理 SendInput。本轮物理 F1 未证明；真实回环 TCP reset 恢复也不证明公网 Kimi 根因。

## 已读与原生验证方法

- 完成序列可能与回合显示顺序不同；用早回合晚完成、尾项较低 seq 和隐藏历史构造边界，核对最大已显示序列且不超过冻结上限。只检查尾行会漏掉游标不前进的问题。
- 原生按键准备会被外来输入改变。最终 HWND/focus/selection/page 检查与 SendInput 放同一次 UI action；失配返回明确未发送，只有未发送键可有界重新准备。实际已注入的键不可重发以掩盖失败。
- WASAPI 静音时可不提供 callback packet。按包时间戳和墙钟对齐真实 PCM，静音间隙填零；同时核对时长、采样率、peak/RMS。非静音与 SDK 成功仍不能证明中文内容，保存 WAV 并另做听验，不用固定词表识别制造通过。
