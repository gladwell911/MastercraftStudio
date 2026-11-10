# 语音本地先录独立验证

2026-11-10，engineer；基线 `98016ccdb47f629888d8226900bbcc8272b0df1b`，验证当前主树未提交实现。只写本报告，不修改产品或测试代码。

结论：核心实现及本次受影响的定向自动验证通过，未发现可复现的新增缺陷。完整现场验收尚未通过：声学出声 P95、真实云端句首识别、4–8 小时稳定性仍无证据。两个 wx 整文件存在 26 个已核实的基线失败，不计为全绿。

## 命令与证据

- 独立运行 `.venv/Scripts/python.exe -m pytest tests/test_main_unit.py tests/test_voice_controller_unit.py tests/test_realtime_asr_unit.py tests/test_voice_input_integration.py -k "voice or fifo or receiver or extract_text" -q`：**47 passed, 982 deselected in 9.56s**。路径包含 voice 的三个核心文件全部纳入，独立覆盖 director 报告的核心 30 项。
- 独立串行运行 `.venv/Scripts/python.exe -m pytest tests/test_voice_input_ui_automation.py tests/test_voice_input_e2e.py -k "local_ready or queued_ready or actual_capture" -q`：**4 passed, 37 deselected in 1.26s**。真实 wx 消息泵，受控设备与协议传输，不是物理录音/云端转写。
- `git diff --check`：通过。检查 main/speech/ASR 产品 diff、所有核心测试与四个新增 wx 案例，未见 GUI 同步等待设备或 Future；核对有界 FIFO、代次事件门禁、单一终包、终态原子接纳和关闭路径。
- 复用 director 的 wx 整文件证据：`C:/Users/gladwell/AppData/Local/Temp/mc-voice-baseline-6is0yu3m/{baseline,current}-result.txt`，分别 **11 passed / 26 failed** 与 **15 passed / 26 failed**。独立脚本逐条比较 FAILED 行：26 条完全相同；基线目录的 main.py、speech_input.py、realtime_asr.py 及两个 wx 测试文件与 `git show 98016cc:<path>` 规范化 CRLF 后完全相同。失败集中于既有 model combo、chat-arrow、notes Alt-X。当前代码/测试与提供证据的同次实现一致，新增四项又已独立重跑，故不重复整文件回归。
- 另以 `.venv/Scripts/python.exe -` 执行独立只读内存探针：设备 open 抛 OSError 后一次错误/无结果/回 idle；receiver 提前 EOF、服务端 code=17 均拒绝；CLEANUP_TIMEOUT=.02 且 session.close 挂起时报告 cleanup_error，退出后 asyncio 仅余探针主任务。四项均 **PASS**，未修改仓库测试。

## 矩阵核对

| 规范场景 | 已执行证据与边界 |
| --- | --- |
| 慢连接、本地 ready、句首 FIFO | integration `test_local_ready_precedes_connection_and_early_stop_preserves_all_pcm` 与 wx capture：A/B/尾帧 C 字节、顺序、WAV 头一次、空负序终包一次通过；真实设备 5s 门延迟另见下文。真实云端开头短语尚未验证。 |
| start 后无首帧、设备失败 | `failures_are_atomic[no_frame]` 及独立 open OSError 探针通过，无 ready/部分结果。 |
| 先连接后首帧、失败/ready 竞争 | 实际设备 0s 连接先完成、随后本地首帧触发 ready；queued-ready 取消/关闭与 overflow/early-final 通过。所有同刻竞争交错不能据此穷尽证明。 |
| 连接前 stop、最后回调 | early-stop integration 的 stop 尾帧与 deadlines[connect] 通过，保留同一 started_at、不重置预算。 |
| starting/recording/finishing cancel | 参数化三阶段全部通过，无结果/错误，资源关闭。 |
| 左右 Ctrl、三/四击、重复 stop | 左右 unit + 两个 wx double-Ctrl 参数案例通过，一次流/连接/stop；相同文本两代次与 direct/optimize 通过。物理全局键盘 hook 未测。 |
| FIFO 边界、背压 | 恰好 640000、再一字节失败、6401 单帧失败、seal、合并唤醒、full/overflow/drain deadline 通过。 |
| 连接/发送/服务端异常、EOF、无 final | connect/send/early_final、三类 deadlines、cleanup failure 通过；补充 EOF/server-error/cleanup-timeout 探针通过。真实 DNS/TLS 故障不在本轮模拟范围。 |
| close、排队 GUI、旧结果 | queued-ready persistence-veto、queued cancel/new-generation 与两次同文通过。 |
| 100 生命周期与资源 | 原有新增压力例独立通过：每轮 stream/ws/session closed、最终 asyncio 只余查询任务、单一音频 worker；fixture shutdown 后音频线程退出。未做 OS 句柄计数分布或长期稳定性验收。 |

## 实际设备及消息泵补测

隔离 Python 进程使用默认 `Microphone (Shure MV88+)`、产品 `VoiceInputController`、真实 `sounddevice.RawInputStream`、真实 wx.App/Yield/CallAfter；仅将 ASR HTTP/WebSocket 替换为 VoiceRig 内存服务，PCM 仅暂存在内存、未写盘或发送网络。以两次 `on_ctrl_keyup(side='left')` 模拟双 Ctrl 接纳；ready 回调只打单调时间戳，没有播放音效、调用实际全局键盘 hook、显示日常窗口或写入编辑器/用户数据。

20 次 0s 连接门延迟，接纳前双 Ctrl 方法调用至 ready GUI 回调：234/234/235/235/250/250/234/235/234/250/235/235/234/234/234/234/250/235/234/234 ms，nearest-rank P95 **250ms**。首帧至 ready GUI 回调 **0–16ms**。另 3s/5s 连接门延迟各一次，对应 ready **219/250ms**，均先于 connected。22 次无产品错误，44 个音频协议包（每次数据包加终包），缓存峰值各 6400 字节；停止在 ready 后立即接纳，因此这组只证明首帧缓存与早停行为，不证明持续语音句首识别。

同期后台每 10ms 投递 wx.CallAfter，取得 **1102** 个 GUI 投递响应样本，nearest-rank P95 **15ms**。这是最小 wx 消息泵和真实设备的隔离响应数据，不能替代完整 ChatFrame 现场性能；完整 ChatFrame 受控消息泵案例已另行通过。

首次测量探针由于 PowerShell stdin 中文比较编码导致等待 ready 观测超时（controller 已 recording、无产品错误），finally 已关闭设备/线程；修正 ASCII Unicode 转义后上述完整测量通过。未把探针错误归为产品缺陷。

**这些回调时间戳不等于音效 API 调用，更不等于扬声器出声。不能据此宣布“20 次声学 P95≤500ms”通过。** 0/3/5s 门延迟不是实际云端网络，5s 样本只有一次；4–8h 复测未运行，历史长时间变慢的根因仍未知。

## 资源与遗留

所有本轮 pytest/内存探针进程已退出；实际设备测量最终断言 shutdown_done、音频线程退出，探针 ping 线程 join，mock 恢复，打印 CLEANUP_OK。未创建测试包、工作树、录音、数据库、日志文件或额外临时目录；只新增本报告。director 的既有 baseline 证据目录保留供上游复核，本轮未删除其他任务文件。未退出/重启日常 MC、打包或操作用户数据。

## Review 修复后的独立复验

同日 director 按 review 修复终包 send/final 竞争、重复 FIFO 失败取消导致过早收敛、diagnostics 排队结果时间戳丢失，并增加异常及优化 GUI 测试。已重新读取增量实现与 fixtures；以下结果更新先前对应软件验证证据。硬件、声学、长稳边界保持不变，不重跑未变化的硬件测量或 26 项无关旧失败。

按顺序独立执行：

1. `.venv/Scripts/python.exe -m pytest tests/test_voice_controller_unit.py tests/test_realtime_asr_unit.py tests/test_voice_input_integration.py -q`：**41 passed in 1.21s**。
2. `.venv/Scripts/python.exe -m pytest tests/test_main_unit.py -k voice -q`：**17 passed, 982 deselected in 9.61s**。
3. `.venv/Scripts/python.exe -m pytest tests/test_voice_input_ui_automation.py tests/test_voice_input_e2e.py -k "local_ready or queued_ready or optimize_worker or software_ready or actual_capture" -q`：**9 passed, 37 deselected in 2.70s**（UI 7 项加左右 Ctrl 协议链 2 项）。

重点核实：

- `test_final_during_terminal_send_waits_for_success[False/True]` 两分支实际通过：final 已到达而 send 被门阻塞时保持 finishing、无结果/错误；发送成功后才接纳，发送失败则一次错误。receiver 等待的是同一个已启动终包发送任务；sender 原有 drain deadline 和 finally 取消仍负责挂起发送的边界。
- `test_failure_wakeups_cannot_converge_while_close_is_pending` 实际通过：重复四次 failure wake 后 cleanup 尚未完成时 network_done/converged 仍 false、socket 仍未关闭；放开 close gate 后才收敛。FIFO 失败取消有单次门禁，controller finally 再等待 cleanup。
- `test_diagnostic_record_tracks_queued_result_delivery` 实际通过：cleanup 形成的有界 diagnostics 持有本会话 metadata，排队结果 GUI 执行后补记 `result_gui >= result_posted >= cleanup`；记录值均为 float。
- `transport_and_device_failures...` 的 EOF/error/start/stop/close 五分支实际通过，验证一次错误、拒绝结果与下一次恢复。fake device close 特意模拟“已释放句柄后抛错”，此例只证明该语义，不证明任意原生 close 失败均能释放。
- `unreclaimed_network_close...[failed/uncompleted]` 两分支明确先断言 cleanup_error、ws **未关闭**、session 已关闭；随后测试主动在网络 owner loop 回收该 fake，才进入 fixture 的 assert_closed。没有把未释放资源默认为已回收，也未把这些受控异常当作真实驱动级释放保证。
- 优化 worker 的正常/new_session/cancel/close 四种 GUI 投递实际通过；软件 ready<500ms 用例通过，仅是受控设备的调度防回归阈值，不能替代原计划 50ms 现场目标或声学指标。

本轮结论：上述局部修复通过独立复验，无新增可复现缺陷。三个测试进程均正常退出，相关 fixture 完成 controller shutdown、音频 worker join 与 fake 资源关闭；无新增临时文件/后台进程，无用户数据操作。最终 `git diff --check` 通过（Git 的 LF→CRLF 提示属于工作树换行提示）。

## 新dog V1测试补强后的独立复验

2026-11-10，engineer在 `22284df21b428800384d32de74450a4980c93ad4` 核对测试增量：每代至少两个不同累计interim、一个不同完整final通过真实receiver/controller/converge；direct/optimize及两代同文各一次均覆盖，默认fake仍不发送interim，产品逻辑未变。

独立执行 `.venv/Scripts/python.exe -m pytest tests/test_voice_input_integration.py::test_latest_text_and_identical_utterances_in_distinct_generations -q`：**2 passed（0.06s）**。核对director的隔离纯内存拼接/只保留首版变异设计与两模式预期失败证据，复用同一未变代码的核心41、main voice17和串行wx9结果。没有重做硬件22次、真实云端、声学/长稳或26项无关旧失败。测试进程退出，工作树干净，无遗留测试资源。此补强关闭新审查V1验证缺口，不改变原审查defer边界。
