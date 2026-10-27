# 可复用经验

## 通知流部署核实

- 源码、构建文件、安装进程和数据库身份分别核对。可从实际 PyInstaller PYZ 检查 capability/表实现；再比对手机协商的 feed 与运行库 meta。更晚的构建时间不证明包含某提交，较低序列也不证明应重置身份。
- 诊断按同一事件拆分 durable/outbox、received/ACK、gate、NotificationManager posted 与系统可见；terminal 在 gate 前 ACK，不能把它报告为已经通知。正常已读取消也不应当作投递失败。
- 独立 frozen MC 的父目录决定 history 和单实例键；测试 OneDrive、token、显式 NATS TCP 端口均隔离，并启动后核 WebSocket 实际监听 owner。清理只按自有 PID 与完整路径，不按进程名。

## 有界维护与验证

- 双范围过滤可能在任一索引上留下线性残余扫描。先在安全前沿内按索引取有界候选，再筛年龄，并用实际入口的VM步数验证十倍数据增长；同时测近期零候选和旧记录低前沿，不把LIMIT本身当证明。
- 发布确认水位必须独立于可丢弃outbox保存，按pair/domain事务推进且不越过未确认前序；清理后复跑重复ACK、重启、去重和权威回放，不能用剩余MAX重新推断已确认历史。
- 后台worker在最终退出判定后新增await会形成唤醒窗口。通过public drain入口暂停维护、加入新final并再次drain，核对所有worker结束后无遗留待发；不要只测私有维护helper。
- 无缓冲write按实际返回字节数推进，0/None无进展必须有界失败；日志落盘异常仍排空子进程管道，诊断只用固定安全标识。普通直接子进程EOF不能覆盖后代继承stdout，真实fixture清理必须固定所拥有的句柄而非进程名。
- 打包源侧过滤只看明确收集根内的相对路径；用test/tests祖先验证必要资源保留。Python TOC与PowerShell复制分别测试，自定义spec、根/嵌套产物和真实复制输出是必要边界。
- 性能基准在只读源的SQLite备份上修改临时库，剥除全部新schema后测真实初始化；校验pair并分开全库/scope计数。不同数据集、轮次和总elapsed/写锁时间不能混报。

## 2026-10-19 外部入口与跨端排障

- 核实际MC模式/domain、frpc目标、同token三处PONG身份及手机日常列表。持久domain写入后当前MC不动态重读，由用户重开；用户环境不自动刷新已开VSCode/MC。
- 回复延迟按同answer_seq对齐outbox和手机接收，后台收到不等于详情显示；通知等待或别次RPC超时不能替代同事件证据。探针和历史快照只证明对应版本/路径。

- Codex 执行审批沿用 `codex_approval_policy` 保存三种模式：`never` → `never / danger-full-access / user`，`on-request` → `on-request / danger-full-access / user`，`auto-review` → `on-request / danger-full-access / auto_review`，依次对应 approvalPolicy、默认 sandbox、approvalsReviewer。访问权限独立保存：缺失或无效值回退完全访问，已有有效权限不随审批方式改变；迁移测试覆盖三种有效权限的保存恢复。缺失或未知值回退 `auto-review`；旧无版本配置一次迁移并保存 `codex_approval_defaults_version: 1`，以后保留手选。只影响后续创建/恢复，切回旧模式显式发送 `user`。自动审查由 app-server 内置审核决定，不保证批准。隔离 wx 菜单与 IPC 至 JSON-RPC 请求验证不代表可见浏览器端到端通过。

- 可携带网页入口使用运行态 `MC_BROWSER_OPEN_ARGV` JSON 数组，PowerShell call operator 展开参数；源码/frozen 路径与 cwd 分离。用户技能根与随包根合并去重、forceReload；安装包由 console worker 提前分派 helper，不输出 worker ready。自有 target 创建后立即 flush ID，后续失败只续验该目标；最终以同一 HWND 可见、非最小化及真实 foreground 判断。跨机首启和焦点效果须在目标机实际验收。

- PowerShell 5.1 接收 argv JSON 时直接赋值 `ConvertFrom-Json` 的结果，避免外层 `@(...)` 把数组包装为单个元素；按技能示例实际展开参数检查。CLI 的 JSON 错误输出使用 ASCII 转义，避免 GBK 无法编码字符再次抛异常、遮蔽原始失败。
- Windows 非管理员打包检查保持有效；当前工具宿主为管理员时，可经现有桌面 Explorer 的 ShellExecute 启动同用户隐藏普通权限宿主，并核实实际 isAdmin=false 和最终退出码。ShellExecute 返回或新建 Shell.Application 本身不证明降权成功。

- 临时入口切回正式安装时，同时核对手机实际 endpoint、编译期 StartupOverride、电脑运行身份及隧道指向。电脑重打包不会修改手机目标；完整 RC 启动参数优先于持久偏好。用同 token 的直连/桥接/公网 PONG 与相同 server_id 核准电脑，再以实体列表和应用重开验证手机。

- 认证诊断要在同一入口、同一 token 下比较实际 CONNECT 字段并收到 PONG。HTTP101/INFO 只证明到达 broker；旧 `auth_token` 与新 `mobile/password` 不兼容时，不能把认证拒绝归为网络超时。运行配置和安装包必须独立于 Git 源码核对。
- 并行测试使用不同父目录和聊天库，单实例键绑定规范化数据路径；独立端口和临时 connector 不接管日常隧道。`-BuildOnly` 仍更新给定目标目录，必须先确认目标独立，再关闭自动启动。

- 辅助 worktree 的 dirty 文件不一定包含遗漏功能：先比较最终文件和主分支、确认提交祖先，再保留补丁并删除已合并分支。推送后核对远端主分支 SHA，避免把本地提交或安装完成当作 GitHub 同步完成。

- 实际隧道验收要同时核新 NATS 进程的 token 与监听端口，再用同一应用 token 比较新 broker 直连、MC 18080 bridge、公网 `/nats` 三处 `INFO.server_id` 并完成 `PING/PONG`。旧 broker 占用 18081 时，新进程可能选择 18082；只看到公网 WebSocket 打开不足以证明连到当前 MC。默认代理可能使 WebSocket 探针失败，本次仅对请求禁用代理后链路成功，不改系统代理。
- 双机身份换名后的启动配置按显式 pair 环境变量、已知计算机名、同身份持久值/default 顺序核对；切到新身份时先确认旧 domain/token 未继承。运行中的 MC 不会因修改 `remote_machine.json` 自动重载，本次由用户手动重启后才验证运行配置。

- Kimi prompt 边界：REST 的 origin 通常位于 metadata.origin；存在该键时优先使用，仅缺失时兼容顶层 origin。role=user 且字典 origin.kind 精确为 injection 才跳过，未知或畸形值保守作为真实问题。回答、思考、分页与旧 owner/alias 恢复共用判定；用真实外层 frame、保存及 SQLite done 状态检查完整恢复链，真实下一问题仍须隔离。
- BMAD 旧模板兼容：在 renderer 输入端规范化旧配置、workflow 和 snapshot 标记，保留严格缺值/链接检查；已解析内容不再作为模板递归渲染。配置答案放项目本地忽略文件，不沿技能链接改写全局来源。兼容用例须覆盖正文中的运行时占位符和不透明配置值。
- Windows 中文修改：PowerShell 管道的默认编码可能让 Python stdin 中文变成问号；采用 apply_patch 或明确 UTF-8 文件输入。文本替换之后检查实际 diff 和正文，再报告完成。

- 回答时间：问题与权威答案分别记录，完成重放不刷新。验证必须经过已有流式回答行的真实 provider 完成和 quiet/deadline；字段已写或重载后正确不能代替实时表面检查。投影重算保留选中和焦点，pending delta 不全量重建。
- Codex 命令审批：主动策略选择作用于后续启动/恢复，实际 request 和 reply 均复核 owner/generation/client；关闭按拒绝处理。单独修改启动策略不能代替完整的交互回复链。
- 详情纯文本投影需保留链接目标、显式空行、列表层级和表格列，HTML 属性解析须允许无值或非法序号。用真实 Markdown 和 HTML 两类输入核对内容；窗口转换放在销毁 finally 的保护范围内。换行边界只维护末尾状态，避免每个段落拼接累计全文。
- 上下文专用请求不能吞掉完整信息刷新：合并一次完整刷新意图，请求完成后重新核对 dialog/owner，再补发。分别覆盖成功、失败、关闭、owner 切换；IPC 标志通过实际 client 序列化到 worker 消费验证，不能只在测试里手造字段。

- 聊天活动：发送受理和首次成功权威回复才更新活动时间；失败终态与重放不更新，保留置顶组。直接更新历史单行而非只排 history dirty，保持身份和焦点；用更晚的竞争聊天与严格时间增加区分发送和收到回复的两次更新。

- OneDrive 数据：源码与打包版的笔记、常用命令路径不同。打包版须在初始化前验证目标存在、SQLite 完整且表结构属于已知笔记库。在线 SQLite 备份生成的是定时快照；先关闭备份连接，再校验并放置文件。两台 MC 可保持连接，用户保证共享数据不并发写入；换机写入前等同步，不能直接用另一台旧库覆盖目标。
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

- 双来源 fixture 的 history/state 与通知必须共用实际 ChatStore 的 message_id/generation/answer_seq；生产序列化缺失会让答案可见却无法确认阅读。用真实 canonical 元数据断言及非零通知到读清零链路验证，不只检查初始零标签。
- 完成序列可能与回合显示顺序不同；用早回合晚完成、尾项较低 seq 和隐藏历史构造边界，核对最大已显示序列且不超过冻结上限。只检查尾行会漏掉游标不前进的问题。
- 原生按键准备会被外来输入改变。最终 HWND/focus/selection/page 检查与 SendInput 放同一次 UI action；失配返回明确未发送，只有未发送键可有界重新准备。实际已注入的键不可重发以掩盖失败。
- WASAPI 静音时可不提供 callback packet。按包时间戳和墙钟对齐真实 PCM，静音间隙填零；同时核对时长、采样率、peak/RMS。非静音与 SDK 成功仍不能证明中文内容，保存 WAV 并另做听验，不用固定词表识别制造通过。

## 全局技能与打包验证方法

- 整目录 symlink/Junction 可以让后续新增、修改和删除的技能直接可达；逐个技能创建链接会漏掉新增目录。旧普通目录迁移到唯一 legacy 路径，失败恢复旧内容；Windows 无 symlink 权限时使用 Junction。清理外链只删节点，检查目标字节与属性不变，不能沿链接修 BOM。
- schema/mock 只能证明请求形状和调用顺序，原生发现另用非模型 app-server 请求验证。比较全局 home 与私有 home 时保持 extraRoots 等配置相同，核对完整记录和顺序；不能从同名记录存在推断模型最终选中了哪项。
- Windows 原生程序可能通过 KnownFolder 读取真实 `.agents/skills`，仅设置临时 USERPROFILE/HOME/CODEX_HOME 不足以证明完全隔离。核对所有返回的技能路径并分别记录读写边界，不能只保留探针名称后宣称完全隔离。
- app-server 重启先显式结束旧进程的 pending 请求，再使旧 reader 失效；用真实等待线程验证立即唤醒，另确认旧 reader 不会失败新进程请求。
- Codex client/worker/packaging 三个纯测试可用 `--noconftest -o pythonpath=.` 避开 autouse wx.App；依赖原有 GUI/数据库夹具的测试仍使用对应 conftest。打包逻辑用临时 DistPath/WorkPath、假构建器和假启动覆盖成功与失败，不运行真实打包或安装版。
