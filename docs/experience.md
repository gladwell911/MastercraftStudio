# 可复用经验

- Kimi prompt 边界：REST 的 origin 通常位于 metadata.origin；存在该键时优先使用，仅缺失时兼容顶层 origin。role=user 且字典 origin.kind 精确为 injection 才跳过，未知或畸形值保守作为真实问题。回答、思考、分页与旧 owner/alias 恢复共用判定；用真实外层 frame、保存及 SQLite done 状态检查完整恢复链，真实下一问题仍须隔离。
- BMAD 旧模板兼容：在 renderer 输入端规范化旧配置、workflow 和 snapshot 标记，保留严格缺值/链接检查；已解析内容不再作为模板递归渲染。配置答案放项目本地忽略文件，不沿技能链接改写全局来源。兼容用例须覆盖正文中的运行时占位符和不透明配置值。
- Windows 中文修改：PowerShell 管道的默认编码可能让 Python stdin 中文变成问号；采用 apply_patch 或明确 UTF-8 文件输入。文本替换之后检查实际 diff 和正文，再报告完成。

- 回答时间：问题与权威答案分别记录，完成重放不刷新。验证必须经过已有流式回答行的真实 provider 完成和 quiet/deadline；字段已写或重载后正确不能代替实时表面检查。投影重算保留选中和焦点，pending delta 不全量重建。
- Codex 命令审批：主动策略选择作用于后续启动/恢复，实际 request 和 reply 均复核 owner/generation/client；关闭按拒绝处理。单独修改启动策略不能代替完整的交互回复链。
- 详情纯文本投影需保留链接目标、显式空行、列表层级和表格列，HTML 属性解析须允许无值或非法序号。用真实 Markdown 和 HTML 两类输入核对内容；窗口转换放在销毁 finally 的保护范围内。换行边界只维护末尾状态，避免每个段落拼接累计全文。
- 上下文专用请求不能吞掉完整信息刷新：合并一次完整刷新意图，请求完成后重新核对 dialog/owner，再补发。分别覆盖成功、失败、关闭、owner 切换；IPC 标志通过实际 client 序列化到 worker 消费验证，不能只在测试里手造字段。

- 聊天活动：发送受理和首次成功权威回复才更新活动时间；失败终态与重放不更新，保留置顶组。直接更新历史单行而非只排 history dirty，保持身份和焦点；用更晚的竞争聊天与严格时间增加区分发送和收到回复的两次更新。

- OneDrive 数据：源码与打包版的笔记、常用命令路径不同。打包版须在初始化前验证目标存在、SQLite 完整且表结构属于已知笔记库。在线 SQLite 备份生成的是定时快照；先关闭备份连接，再校验并放置文件。跨机不并发运行 MC，换机先退出并等同步；不能直接用另一台旧库覆盖目标。
- wx 可访问性：可见状态未变时不重绘列表、不移动焦点。后台事件先合并，再按 chat、turn、session 与 generation 复核归属；真实定时器测试负责完整清理。
- 远程事件：provider 按规范化 model id 分派；可继续接收输入的 CLI 客户端绑定 chat_id。Kimi 公开 thinking 与工具调用确认的中间说明直接进入实时 canonical 执行投影；REST /messages 在完整 prompt 边界内按同 owner/步骤补齐，不能覆盖完成快照或补写子代理流。隐藏标记单调清除缓存及持久化公开正文；最终回答仍以持久化 done turn 为事实来源，中间说明不混入终答。
- 终答补发：保存 done 回合后才投递 final/history；短暂失败保留待发项并重试。Codex 事件仅绑定唯一可确认的 turn，不能用最后一轮兜底；纯 Not Loaded / notLoaded 字符串及子代理占位可过滤；混合结果、失败、非零退出及有用描述必须保留。分别检查事件入口与历史投影，不能只匹配整行。
- 执行投影：隐藏页在 worker 准备有界缓存，重复刷新复用在途任务；F1 显示切换不是数据失效，不取消已准备缓存。读取缓存先于历史水合，隐藏完成不重绘或抢焦点；用慢存储、失败重试及旧 owner 回调验证。canonical 步骤与远程快照同源，分页绑定 owner/revision；全量投影、哈希和快照放 worker。
- Kimi 恢复预算：等待后再核对 deadline，多 owner 共享预算须覆盖后续 owner 一进入就过期。错误优先保留原始及最后真实异常，无真实错误时才用超时兜底；未执行 client.start 不能称作子进程启动失败。
- 验证边界：Local、只读 Live、真实模型、模拟器和实体机分别记录；宽套件既有失败按精确用例复核，不用定向通过替代全量结论。

- 启动归属：chat_id/turn_idx 在清空后可能复用，回调到 UI 时仍核对原 turn/request 与来源 client。Codex 启动 generation 与绑定 thread 后的 context generation 分开；新请求未 ACK 的事件才缓冲，快速回答在 ownership ACK 之后交付。新请求失败仍保留先前 native owner，溢出明确失败，晚到 final 不复活失败请求。

案例、协议细节和其余经验见 [归档原文](archive/entry-context-2026-09-29/experience.md)。

## 2026-10-01 验证方法

- 先核对交付树 HEAD，不用主树或旧日志代替；锁屏可见像素、对象字段、跨端传输分别记录。
- `Raise()` 和 wx 内部焦点不足以证明 Windows 前台。使用 `tests/owned_window_qa.py` 真实点击测试窗口标题栏，严格检查 HWND 后保留原键盘和焦点断言；finally 恢复置顶状态和鼠标，不添加全局键盘钩子。

## 2026-10-04 当前补充

- 执行投影：写入维护 visible 位，初始化一次原子回填，部分索引获取可见尾部。LIMIT 不证明扫描工作有界；用大量隐藏后缀与 SQLite 指令数验证。F1 展示当前 owner/view/turn/revision 的真实尾页，dirty 只使扫描新鲜度失效，worker 补齐；冷打开通过可视索引获取真实内容。
- Kimi 自有 REST Session 在共享前设置 trust_env=False，WS 绕过本机代理；GET 最多一次剩余预算内恢复，POST 结果不明不重发。provider 的 10054 仍是失败，不得根据错误文字假称恢复。
- 桌面验证区分 HWND WM_CHAR/BM_CLICK、wx F1 事件、窗口键消息与物理 SendInput；对应 UI 路径通过不证明物理按键送达，真实回环 TCP reset 恢复也不证明公网 Kimi 根因。

## 已读与原生验证方法

- 完成序列可能与回合显示顺序不同；用早回合晚完成、尾项较低 seq 和隐藏历史构造边界，核对最大已显示序列且不超过冻结上限。只检查尾行会漏掉游标不前进的问题。
- 原生按键准备会被外来输入改变。最终 HWND/focus/selection/page 检查与 SendInput 放同一次 UI action；失配返回明确未发送，只有未发送键可有界重新准备。实际已注入的键不可重发以掩盖失败。
- WASAPI 静音时可不提供 callback packet。按包时间戳和墙钟对齐真实 PCM，静音间隙填零；同时核对时长、采样率、peak/RMS。非静音与 SDK 成功仍不能证明中文内容，保存 WAV 并另做听验，不用固定词表识别制造通过。

## 全局技能与打包验证方法

- 整目录 symlink/Junction 可以让后续新增、修改和删除的技能直接可达；逐个技能创建链接会漏掉新增目录。旧普通目录迁移到唯一 legacy 路径，失败恢复旧内容；Windows 无 symlink 权限时使用 Junction。清理外链只删节点，检查目标字节与属性不变，不能沿链接修 BOM。
- schema/mock 只能证明请求形状和调用顺序，原生发现另用非模型 app-server 请求验证。比较全局 home 与私有 home 时保持 extraRoots 等配置相同，核对完整记录和顺序；不能从同名记录存在推断模型最终选中了哪项。
- Windows 原生程序可能通过 KnownFolder 读取真实 `.agents/skills`，仅设置临时 USERPROFILE/HOME/CODEX_HOME 不足以证明完全隔离。核对所有返回的技能路径并分别记录读写边界，不能只保留探针名称后宣称完全隔离。
- app-server 重启先显式结束旧进程的 pending 请求，再使旧 reader 失效；用真实等待线程验证立即唤醒，另确认旧 reader 不会失败新进程请求。
- Codex client/worker/packaging 三个纯测试可用 `--noconftest -o pythonpath=.` 避开 autouse wx.App；依赖原有 GUI/数据库夹具的测试仍使用对应 conftest。打包逻辑用临时 DistPath/WorkPath、假构建器和假启动覆盖成功与失败，不运行真实打包或安装版。
