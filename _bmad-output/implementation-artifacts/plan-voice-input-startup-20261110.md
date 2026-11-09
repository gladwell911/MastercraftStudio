---
title: '双 Ctrl 本地立即采音与后台 ASR 建连'
type: 'bugfix'
created: '2026-11-10'
status: 'planned'
scope: 'plan-only'
context:
  - 'D:/code/sj/mc/AGENTS.md'
  - 'D:/code/sj/mc/docs/handoff.md'
  - 'D:/code/sj/mc/docs/experience.md'
  - 'D:/code/sj/mc/docs/reflection.md'
  - 'D:/code/sj/.diagnostics/voice-slow-20261109/report.md'
  - 'voice-input-startup-20261110/ARCHITECTURE-SPINE.md'
---

# 实施计划

## 目标与证据边界

双击 Ctrl 后，本地麦克风成功开始采音即播放开始提示；云端识别连接在后台并行建立，连接成功后按原顺序发送已经录到的音频，保住句首。保持左/右 Ctrl、单 Ctrl 停止、直接输入与现有优化模式的结果行为。

现场运行包已捕获 GUI 在 `speech_input.py:112` 同步等待 ASR start 约1.4/2.5秒；第三次设备初始化只占一个约33ms样本。未直接测扬声器实际出声、未细分网络握手，也未证明运行数小时后变慢的具体机制。本方案不以这些未证结论为前提。

当前任务只产出计划；不实现、不测试日常录音、不打包、不退出或重启 MC。数值阈值为本计划的默认实施选择，验收前通过测试校准，不是现有性能保证。

## 必要变更位置

| 位置 | 当前问题 | 计划职责 |
| --- | --- | --- |
| `speech_input.py:VoiceInputController` | GUI持锁等待Future；回调引用可变`self._asr_client`；cancel也同步等待 | 引入会话generation、状态协调、单音频工作线程、短锁PCM桥接、异步stop/cancel/shutdown |
| `realtime_asr.py:RealtimeAsrClient.start` | 每次重置_queue；_running在连接成功前即true；失败可留部分session | start不重置已录FIFO；连接任务受deadline控制；finally关闭半初始化资源 |
| `realtime_asr.py:push_audio/_sender/stop/_receiver` | 满队列静默丢；stop依赖running并等receiver；缺专用abort | 单消费者有界FIFO、seal/排空/终包、finish与abort分离、异常EOF/任务失败统一收敛 |
| `main.py:VoiceInputController初始化/_on_voice_state/_on_voice_error/_on_close` | ready音依赖回调执行时state；已排队callback可过时；close仅cancel | GUI执行时校验generation/closed；ready按事件一次播放；可访问错误提示；关闭发shutdown而不等网络 |
| `tests/test_voice_controller_unit.py` 等 | 多为快捷键/插字回调替身 | 补充真实状态竞争、慢连接、音频顺序和资源收敛测试 |

不改 `GlobalCtrlTapHook` 的200ms检测窗口/60ms去重，不混入数据库保存、远程控制或实时通话优化。

## 状态、线程与接口约定

状态由会话协调器的短临界区更新；网络连接另有 connecting/ready/failed 子状态，不能用网络状态冒充录音状态。

| 状态 | 含义 | Ctrl 行为 |
| --- | --- | --- |
| idle | 无活动会话且设备已回收 | 同侧双击创建generation并进入starting |
| starting | 设备打开/等首帧，网络并行建连 | 单击接受为取消本次开始；不播放开始/结束成对录音音，不产生结果 |
| recording | 设备start成功且首帧已入队；网络可以仍connecting | 单击进入finishing，立即接纳停止并发结束提示一次 |
| finishing | 请求停止设备，等回调静默、seal、排空与识别final | 忽略重复Ctrl，不再开流、不重复终包 |
| cancelling | 代次已失效，设备/网络后台清理 | 忽略Ctrl；资源回收完才回idle |
| closed | 永久关闭控制器 | 所有输入/迟到事件忽略 |

失败与成功都经统一终态收敛至idle；closed不回idle。键盘检测器在会话开始/终结时重置，已用于触发开始的第二次抬键不再解释为停止。三击/四击的后续抬键最多接纳一次停止，starting/finishing/cancelling不重复创建连接或流。

- GUI：同步仅保留命令接纳、generation分配、状态快照及排队；禁用`.result()`、设备open/stop/close与join。回调到GUI后再次检查会话代次和窗口closing。
- 一个音频工作线程：串行设备生命周期；按会话捕获stream与buffer，不能从旧闭包读取“当前client”。收到取消/停止意图后，即使open/start稍后才返回，也只关闭自身stream，不发ready。首帧Event可由回调设置，工作线程等待不阻塞GUI或ASR loop。
- sounddevice回调：复制PCM、在独立短锁下追加FIFO/设置首帧信号；不进行网络、等待Future、设备停止或GUI调用。输入overflow状态不得直接return后继续成功；记下失败并合并通知协调器。
- 一个常驻ASR loop：拥有ClientSession/WebSocket和connect/sender/receiver/finish/abort任务。循环只初始化一次并有ready握手，避免旧`is_running()`竞态创建两条线程。音频设备回收不放在该loop阻塞它。
- 回调事件携带generation、事件类型及payload；状态事件、结果、错误均绑定同一会话。GUI已接纳结果一次后不再插入重复final；不能用文本内容去重，用户两次说相同内容仍插入两次。

## 缓存与协议

保持现有16kHz/mono/int16、200ms块。每秒32000 PCM字节；FIFO上限640000字节，即20秒/100个标准块。另最多6400字节发送中；压缩包和WAV头为单帧临时开销。以实际字节计数约束变长回调块，超过单帧上界则失败，不能只数元素。缓存仅内存，不写磁盘。

用一个线程安全有界deque承载连接前和连接后的全部未发送音频，替代“controller缓存＋client另一个大queue”的双积压方案。ASR sender是唯一消费者；连接成功后按FIFO弹出，随后新帧继续追加尾部。跨线程只合并一次wake通知，防止ASR loop暂停期间`call_soon_threadsafe`闭包无限积压；缓存超限即使loop忙也立即设置终态/停止采音意图。

队满不丢旧帧、不静默丢新帧：整次失败，停止采音、abort连接、清缓存、提示“语音缓存已满，本次未写入，请重试”，拒绝部分转写。网络发送失败和输入overflow也不写入不完整识别结果。

sender在连接及初始请求成功后才启动。第一份有效PCM前只拼一次WAV头；保留现有请求序号及协议编码。设备停止且最后一个回调完成后才seal；sender按序排空后只发送一个空音频负序号终包，避免“最后一块同时带终标记＋额外空终包”两种路径并存。成功路径以协议final作为完成条件；服务端提前关闭或返回错误，均走失败清理。

成功final还必须满足设备已停止、FIFO已seal且排空、终包已成功发送；服务端提前final不得提交录音片段，按协议提前结束处理并失败。队满/输入overflow立即在FIFO锁内设置不可逆失败标记，再合并通知；成功结果提交检查该标记、取消代次及上述条件并原子裁决，不仅依赖可能迟到的错误事件。固定锁序为协调器锁→FIFO锁；音频回调只取FIFO锁，不能反向取协调器锁。GUI投递执行时仍复查会话有效性。

## 停止、取消、失败与退出

1. **已采音但未连上就停止：**立即接纳stop及结束提示，音频线程停止/关闭设备，等待回调静默后seal；保留已录PCM。继续等待同一建连任务的剩余时间，成功后发缓存、单一终包、等待final。不能重新连接重置deadline，也不能把stop变成丢掉整段。
2. **设备尚未就绪就停止：**视为取消开始；已缓冲的开机瞬态数据丢弃，不发ready、不转写。音频open/start返回后立刻关闭局部stream。
3. **显式cancel：**先失效generation和关闭采音接纳标记，清FIFO，再提交任务取消与abort；不等服务端final，不发结果，不将用户取消当错误播报。迟到连接成功必须自行关闭，不能重新建立sender。
4. **连接/首帧/识别超时：**建连含初始请求总预算10秒，从启动请求单调时间起算；设备start成功后2秒仍无有效首帧则设备失败；终包发送成功起12秒无final则结果超时。所有send await亦在可取消任务内，已连接且FIFO已seal后，排空及终包发送另设12秒总预算，超时失败，防止背压令“final计时尚未开始”永远挂住。
5. **异常清理：**connect/start、sender、receiver统一异常传播并finally取消兄弟任务；关闭ws/session、清缓存，网络清理总预算3秒。重复stop/abort/close幂等，`_running=False`不能使清理跳过。取消超时Future必须实际取消协程，不能只丢引用。
6. **错误反馈：**区分“麦克风不可用”“识别服务连接失败/超时”“录音数据不完整/缓存满”“识别结果超时”；每会话只播一次错误音，并用既有状态栏/读屏渠道给出原因，避免现在所有启动异常都叫麦克风错误。ready后网络失败仍停止采音并提示此次未写入。
7. **关闭窗口：**`_on_close`发controller.shutdown，立即closed并拒绝所有GUI事件；后台abort并取消ASR任务，释放session，结束并close loop，终止音频worker。网络清理与join不得阻塞GUI。现有持久化关闭流程保持；发生关闭veto时也禁止已失效回调写UI。设备原生调用若无法返回，不能强杀线程或另开第二流，记录不可用并隔离迟到回调；不虚假承诺驱动级无条件及时回收。

## 分步实施与检查点

1. **先补回归契约：**可控FakeStream在start/首帧/停止各设Event，FakeASR连接等待Event；制造现有慢连接下“无法本地ready”的失败测试。先验证异步边界与音频序号，不用数秒sleep等时序。
2. **实现会话与PCM桥：**generation、生命周期、FIFO字节上限、合并唤醒、seal与回调隔离；独立验证满/空/并发seal/取消，不接GUI。
3. **调整ASR客户端：**移除start重建缓存；connect、单sender、receiver、finish/abort分开；timeout/finally清理及单终包测试先绿。保留协议payload和文本提取行为。
4. **控制器并行开始：**非阻塞start，启动音频worker与ASR任务；首帧确认ready，stop/cancel按状态表处理，旧会话资源捕获到局部。禁止GUI持锁等外部操作。
5. **接GUI/退出：**带代次的GUI执行门禁、一次提示/结果、关闭shutdown；更新原先强行同步Thread/CallAfter替身的测试，补真实wx消息泵用例。
6. **定向验证与计划核对：**运行下述必要套件，检查没有日常路径写入。通过后单独提交实施结果供后续部署流程使用；本计划不自动发布或启动。

## 必须覆盖的验证

| 场景 | 可执行判据 |
| --- | --- |
| ASR连接挂起5秒，麦克风立即有帧 | ready先发生，GUI仍处理消息；放开连接后收到PCM A/B/C次序及字节完全一致，句首A未丢 |
| 本地start成功但首帧没到；设备失败 | 无ready；首帧deadline到后一次设备错误及资源清理 |
| 连接先成功，首帧后到；同刻失败/ready竞争 | ready仅本地确认后一次；失败/取消已接纳则无迟到ready |
| 连接前stop；stop遇最后音频回调 | seal含最后合法帧；按序排空；WAV头一次、终包一次；剩余连接预算不重置 |
| starting取消、recording取消、finishing取消 | 无结果、无晚到错误/音效；旧client/stream仅自身被清理 |
| 连续左右Ctrl/三击/快速新会话 | 一次开始最多一个流/连接；重复stop不重复清理；不同侧既有语义保持 |
| FIFO边界与网络背压 | 恰好上限成功，多1字节失败；内存/待处理wake有界，失败不插入部分结果 |
| DNS/握手/发送异常、服务端错误/无final/提前EOF | 对应一次错误；任务/ws/session回收，没有遗留 Future或挂起finish |
| 关闭与已排队GUI事件、旧结果迟到 | 销毁后不碰wx控件；新generation不收旧PCM/文本；相同文字两次会话仍各写一次 |
| 100次start/stop/cancel压力 | 活跃任务/线程/句柄回到稳定基线；未关闭session告警为零，不只观察线程累计名字 |

定向命令（在实施时执行，此次未运行）：

```powershell
.venv/Scripts/python.exe -m pytest tests/test_voice_controller_unit.py tests/test_realtime_asr_unit.py tests/test_voice_input_integration.py -q
.venv/Scripts/python.exe -m pytest tests/test_voice_input_ui_automation.py tests/test_voice_input_e2e.py -q
```

wx相关套件串行，所有frame/timer/新增worker/loop在fixture销毁时清理。实际UI测试使用隔离数据路径与输入承接窗口；不能因为文件名e2e便把现有“直接回调插字”测试当作完整按键/录音验收。

## 测量与现场验收

必要轻量日志：同一generation的hook抬键、GUI接纳、设备open/start、首帧、ready投递/GUI执行/音效调用、连接开始/成功、缓存峰值字节、stop/seal/终包/final、取消/清理完成；使用monotonic时间，仅元数据，不存PCM、识别文本或凭据。日志有界并可关闭，音频回调不直接写磁盘。

验收目标：正常设备上双击第二次抬键到开始音效调用P95≤500ms；本地ready到音效调用≤50ms；将连接延迟设为0/3/5秒时音效指标不随之增加，5秒情况下ready至少早于连接成功，开头短语与编号PCM都完整。自动测试主要断言事件顺序，真实时限在隔离运行态测，避免CI机器负载造成假失败。

至少20次启动记录分布；用户许可的隔离慢网络/可控连接延迟测试，及持续4–8小时后同样一组测量。连接等待期间GUI消息响应P95≤100ms，GUI栈不再出现语音Future.result或设备阻塞。声学出声需另用系统回环/时间对齐录音验证，不能将音效API调用时刻冒充实际听到时刻。当前不自动操作日常MC完成这些验收。

## 可选优化与保留边界

连接复用/预热、DNS/TCP/TLS分阶段网络研究、音频设备预开、专用音频进程隔离均独立后续工作；不作为此次本地先录必需项。不新增持久音频存储，不调整识别服务、凭据、实时通话、文本优化模型或全局快捷键方案。
