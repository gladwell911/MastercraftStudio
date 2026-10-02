# Monkey 四项原因分析（2026-10-02）

源码分析基线：MC 712b3985a476fbf59d571db045654884d6f49c28，RC ef27252dbb40420ec30762b55a2712ca4194f010。仅分析及创建临时设备复现测试，未修改产品代码。

## 1. 锁屏收到通知解锁后仍无详情

确定原因：rc/android/app/src/main/kotlin/com/example/zhuge_qa/RemoteBackgroundService.kt:54-55 创建主 notification 时 `visibleMessage = if (deviceLocked) privateMessage else event.message`，锁屏通知的主版正文已永久写成“新消息”。系统解锁只能从 publicVersion 切换到主版，无法凭空恢复已丢弃的正文。

最小修复：主版正文与 BigText 始终保留 event.message；继续使用 VISIBILITY_PRIVATE 和 publicVersion（聊天标题+新消息）。锁屏遮罩保持，解锁自动展示正文，无需维护 unlock receiver 或重新投递通知。定向 Android 验证锁屏创建 notification 主版 extras 保有详情、公版遮罩，设备锁屏→收件→解锁观察同条通知。

## 2. 点击通知 A 留在聊天 B

确定原因：rc/lib/main.dart:2705 drain 在 `_drainingNotificationTaps=true` 时 await route；:2888 await openSession；:1620 openSession await Navigator.push，实际等到页面退出。用户先点 B 保持页面，再点 A，A 入队却因 drain guard 不会导航，仍看到 B。页面退出才处理下一条。

最小修复：区分“route 已 push”与“route popped 后清理”。通知队列等待导航建立完成即可；保留普通 openSession 原有关闭后的焦点/active-owner清理，不要将整个函数一律 unawaited。还应确保 drain 运行期间新入队项继续处理，避免一次 entries 快照后遗留新 tap。

另外 Android buildLaunchIntent:734-768 使用 requestCode 数字和 UPDATE_CURRENT，Intent 仅 extras 区分 chat，extras 不参与 PendingIntent 身份。notificationId 哈希冲突可能覆写其他事件的 extras。增加 pair/event 唯一 data Uri（保持通知标题和payload）；此项为源码明确身份缺口，未声称已现场复现哈希碰撞。

验证：B 通知打开保持路由→A 通知立即打开 A；冷启动；连续通知/导航过程中新增 tap；不同pair事件强制同requestCode仍保持独立 contentIntent。

## 3. 长耗时答案无时间

确定原因不是累计阈值算法：RC answerTimestampProjection:10543、MC answer_time_projection:690 已按上一显示 anchor 300 秒。数据层与投影层仍把同一 turn 的问题和答案视作同一时间。RC remote_control_models.dart:564 RemoteTurn 只有 createdAt；main.dart:4855 两种 ChatMessage 同取 turn.createdAt。MC :6021按turn idx唯一时间；:5332快速追加按turn idx去重；:6170兼容重绘同样只在turn开头插时间。耗时几十分钟的答案因此永远拿发问时刻，且同turn不能插第二时间。

最小契约路径：持久化 turn 增加答案真实时间；MC _remote_turn_payload:11703 payload 与 cache signature 都携带；RC RemoteTurn parse/本地合并签名携带；两端回答投影按 message role 时间而非 turn 唯一时间。保持 created_at 为问题时间和历史排序依据，不覆写它。注意 RC :177x 和 :4855 两个构造入口，以及 MC增量追加、窗口裁剪重投影、兼容重绘。

已有实际时间事实可优先复用：mc/execution_projection.py:184 timestamp(raw_kind) 扫描同turn canonical question/final execution step ts，缺失才 created_at。历史缺答案时间应从同owner/revision/turn final事实恢复，不能以当前时间伪造旧消息。MC _on_done:19554 与 active/background Codex终结、Kimi终结需共同覆盖，稳定终结重放不能刷新答案时刻。

验证：同turn问题12:00、答案12:31出现独立时间；后续消息12:33不显示、12:36显示；累计短间隔达到300秒；分页裁剪、重载、历史/后台聊天、未知timestamp回退。

## 4. Codex执行行重复朗读：模拟器精确复现

确定原因：rc/lib/main.dart:9119 wrapper regex 要求 commentary/final_answer 后必须空白或结束 `(?=\s|$)`。用户实例是 `阶段：commentary我会...`，没有空白，所以comparisonBody无法去包装；:9129同正文判断失败，返回 title + 中文逗号 + detail。:9256 单个 Semantics label 本身含同正文两次，excludeSemantics:true 无法修复父label中的重复。并非子Text语义和父语义重复，也不是新announce调用。

设备：adb emulator-5554 online；TalkBack 服务已启用。复现前 RC 主包未安装，仅 .test 包，前台为其他工程 com.example.control_tower。未清除任何应用或个人数据，drive安装当前RC debug fixture。实体 63b35d91 未操作。

仅临时测试：rc/integration_test/monkey_repro_temp.dart，复用既有 _ExecutionLogCodexService 和真实 ChatPage，注入用户同文 title 与无空白 wrapper detail，真实进入执行页，读取 owner-qualified Semantics 的 label，断言正文出现2次。

命令（RC cwd）：
`flutter drive --driver=test_driver/integration_test.dart --target=integration_test/monkey_repro_temp.dart -d emulator-5554 --no-pub --no-dds`

exit 0，输出：
`MONKEY_DEVICE_SEMANTICS=我会使用 bmad-build-auto（原label含反引号），先读取流程要求，并检查上一轮定位的聊天回复错位问题及相关项目指令。，开始执行：阶段：commentary我会使用 bmad-build-auto（原label含反引号），先读取流程要求，并检查上一轮定位的聊天回复错位问题及相关项目指令。`

设备日志原文与用户示例完全同构，同一个label正文两次，复现断言通过。这是设备真实Flutter语义证据，不声称已录制TalkBack语音或生产模型调用。

最小修复：仅当已识别有限包装并包装后正文等于另一字段正文时折叠，允许包装与正文直接相邻。可避免宽泛prefix regex误吞 commentaryExtension 等独立内容：用候选去包装结果与对侧全文相等作为证明。保持完整detail换行、不同详情、独立provider item identity。增加无空白包装、中文/反引号边界及非包装近似前缀用例，将临时fixture转为正式设备回归或删除。尚未证明或建议对所有相同provider item跨行文本去重。