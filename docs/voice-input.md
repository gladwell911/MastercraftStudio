# 双 Ctrl 语音输入

本地先录实现在源码中；安装包部署状态另看 [当前交接](handoff.md)。

默认使用豆包流式语音识别 2.0：资源 `volc.seedasr.sauc.duration`，WebSocket 路径 `/api/v3/sauc/bigmodel_async`，请求体 `model_name` 仍为 `bigmodel`。`DOUBAO_ASR_RESOURCE_ID` 和 `DOUBAO_ASR_WS_URL` 可显式覆盖，覆盖时需选用配套接口与资源。

2026-11-10 使用现有凭据和固定测试音频，经真实 `RealtimeAsrClient` 收到“你是谁？你叫什么名字？”最终结果，终包发送与网络清理通过。旧 `/bigmodel` 路径对 2.0 资源返回 HTTP 400，因此同步更新默认路径。这证明源码云端识别链路可用；尚未打包，也未验收物理双 Ctrl、麦克风与焦点插入。

## 使用与反馈

- 同侧双击右 Ctrl 开始录音。识别完成时读取 Windows 活动编辑控件及可用的光标或选区，核对目标后写入。应用拒绝写入或无法确认结果时，MC 通过读屏和错误音反馈。
- 同侧双击左 Ctrl 开始录音。每段追加到 MC 问题框末尾，以全角分号和换行结束。空草稿直接从识别文字开始，控件名称单独表达「输入」和「已发送」。后台追加保持外部前台窗口和控件。相同的两次识别结果各追加一段。
- 麦克风启动且首个有效音频帧入队后播放开始提示，ASR 在后台连接。录音中单击任一 Ctrl 停止并播放结束提示。

设备尚在 starting 时单击 Ctrl 是取消开始，不产生就绪提示或结果；已经 recording 但尚未连接时停止则保留录音、等待原建连预算内完成发送。finishing/cancelling 忽略重复开始/停止。关闭窗口立即使语音会话和排队事件失效，即使持久化暂时 veto 关闭也不再写语音结果。

错误通过状态栏、读屏和一次错误音反馈。麦克风不可用、录音溢出/缓存满、连接或发送失败、结果超时各有原因；失败/取消不插入部分识别结果。

## 实现约定

- `speech_input.py` 的录音代次绑定触发侧、音频、客户端、FIFO 和回调。音频工作线程串行操作设备，ASR 循环拥有网络对象。GUI 执行排队事件时再次校验代次与窗口状态。
- `mc_frame_keyboard.py` 按左右侧记录键盘状态。左右 Ctrl 采用同一个短按与双击窗口。组合键或混合左右短按不会开始录音，钩子和回退轮询按释放事件去重。`windows_voice_target.py` 使用 Windows UI Automation 绑定外部编辑目标，写入后回读正文确认结果。
- `realtime_asr.py` 的单FIFO服务连接前和连接后全部PCM：16kHz/mono/int16，标准200ms块6400字节，排队最多640000字节，另最多一帧发送中。跨线程唤醒合并；超限在FIFO锁内不可逆标记失败，不能静默丢帧后继续成功。
- stop先让设备停稳、回调静默再seal，sender顺序排空，仅发送一个空音频负序终包。只有设备已停、FIFO sealed且排空、终包发送成功、协议final到达且未失败/取消，才原子接纳结果。final可等已开始的终包发送结算；真正提前final仍失败。
- cancel丢缓冲并异步abort；FIFO失败只触发一次取消，清理中不重复打断。会话network_done/收敛等网络清理结算，不能仅凭running布尔值跳过关闭。
- 建连含初始请求从接纳起10秒；设备start成功后首帧2秒；已连接且sealed后排空/终包12秒；终包成功后final12秒；网络清理预算3秒。stop不重置建连预算。清理失败可观察，原生驱动永久不返回或真实取消抵抗的回收保证尚未验收。
- `diagnostics`是最多128条的内存元数据记录，含generation、monotonic阶段时间与缓存峰值；排队GUI结果执行后仍更新其投递时间。不保存PCM、转写正文或凭据，也不由音频回调写磁盘。

更完整的规范及锁序见 [架构约定](../_bmad-output/implementation-artifacts/voice-input-startup-20261110/ARCHITECTURE-SPINE.md)。当前完成状态、命令、审查局限及五项现场边界集中维护在 [handoff](handoff.md)；逐阶段证据见 [独立验证](../_bmad-output/implementation-artifacts/verify-voice-input-local-first.md)。受控协议/软件时延测试与物理按键、声学出声、真实云端识别及长稳验收分开报告。
