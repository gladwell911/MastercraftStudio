# 当前交接

截至2026-10-04。本阶段完成F1即时执行视图、Kimi本机连接恢复及两端消息活动排序，并补充针对性端到端测试。产品提交为MC `7b7be2ac878249f70d5392c621253b57a9bea026`、RC `5ee400c8f4564c8bad0fa3d6267e3a9d20545f40`；后续测试/文档提交以`git log -1`为准。本轮未更新桌面安装包或实体手机。

## 当前实现

- F1首帧使用当前chat/view/turn/revision的已知可视尾页；新步骤使扫描失效时仍保留有效尾页，深历史补齐留在worker。冷聊天正常打开通过可视索引获取真实尾页，避免隐藏占位遮住真实项。清空、切换owner和过期generation回调保持隔离。
- ChatStore的`execution_steps.visible`与最终payload同事务维护；旧库初始化时一次原子回填，`idx_execution_visible_tail`部分索引用于可视尾页。读取不再逐条调用Python predicate，原始payload、step_index和分页cursor保留。SQL LIMIT只约束结果数，不能证明实际工作量有界。
- 发送受理和首次成功权威终答直接更新对应历史行，绕过idle/导航静默等待，保留选中聊天身份与输入焦点。读取、标题、失败和终答重放不产生新的消息活动；置顶组规则保留。RC接纳权威时间，点击详情和状态读取不再置顶。
- Kimi自有本机REST Session禁用环境代理，WS明确绕过loopback代理。GET连接异常最多一次预算内恢复；Session配置后才发布共享引用；POST结果不明不重发。transport_error走owner恢复，权威provider error仍失败，不能仅凭文字含10054就重试。
- created_at/answer_at、累计300秒时间标记、Codex命令审批、通知路由、聊天信息及跨端v3执行投影保留。纯notLoaded占位过滤与UI/存储共享可视规则，有用描述和对应失败内容保留。

## 验证与复现

源码独验：Kimi client87项、恢复/provider25项、RC store/service103项及recency widget/analyze通过。最后索引/迁移/writer/冷聊天/F1定向11项通过；101与3000隐藏尾行的可视查询均386条SQLite VM指令。五个宽Kimi summary/ID用例在修复基线也失败，未扩展修复；这些结果不代表完整产品套件通过。

新增端到端测试由engineer独立复跑，MC cwd、wx GUI串行、app/notes隔离：

```powershell
.venv/Scripts/python.exe -m pytest tests/test_immediate_kimi_local_e2e.py -q
.venv/Scripts/python.exe -m pytest tests/test_codex_ui_responsiveness_automation.py -k f1_first_frame -q
```

分别2项通过。前者通过HWND WM_CHAR/BM_CLICK、真实loopback HTTP和TCP reset，验证受理/成功终答两次竞争排序、焦点保持及问题仅一次POST；provider启动和WS受控。后者覆盖冷历史和执行中owner、3000隐藏尾行、慢worker首帧、新步失效及revision清空；F1采用wx KeyEvent，Tab采用HWND键消息，不等于物理键验证。

RC专用`Codex_Cross_Client_API_35`在本轮为`emulator-5556`，真实UI与私有strict-V2 NATS场景通过，runner exit0。RC cwd，复跑前重新核对专用模拟器serial：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run_cross_client_regression.ps1 -Mode Local -DeviceId emulator-5556 -DesktopRepo D:/code/sj/mc -ImmediateRecency
```

手机重复点击不重排；真实发送ACK后上移；权威后台活动到达后一帧上移；同一session终答bubble可见。harness必须发布完整execution_projection/history_changed，notification ACK不能代替历史更新。专用AVD曾VM连接失败，保数据冷启动、禁快照、softwareGPU和drive/no-dds恢复；未操作5554 Detox。私有harness/temp已清理，日志保留。

工作区证据：`D:/code/sj/_bmad-output/implementation-artifacts/`下的`plan-immediate-execution-kimi-chat-recency.md`、`verify-immediate-execution-kimi-chat-recency.md`、`tests/test-summary.md`及`tests/immediate-e2e-20261004/verify.md`。这些报告/日志不在产品Git仓库；本文件保留复跑命令与结论。

## 接续与边界

- 无本轮已确认待修产品失败。物理F1 SendInput未送达char-hook，独立旧AltY也未送达处理器；保留环境失败证据，不能以wx事件通过代替物理按键验收。
- 用户现场Kimi10054具体诱因仍缺日志。本轮真实本机socket恢复通过，不证明真实Kimi启动/WS、公网Live或偶发断连消失；实体TalkBack、双机及完整产品套件未验证。
- 后续交付先核对安装版本和实际数据路径，再构建、更新及实测；源码HEAD不证明运行包已更新。RC实体包最后已知安装为2026-10-03文件分享阶段，本轮只用专用模拟器。
- 当前两仓库分支`fix/immediate-execution-kimi-chat-recency`未配置上游；收尾保留本地提交，不自行修改remote/upstream或另选推送目标。
- 源码笔记为`D:/code/note/notes.db`；打包版要求个人OneDrive的`code/data/sj/notes.db`存在并经完整性和已知表结构校验。2026-09-29云端库只是快照，切包前补齐后续变化，保留笔记、聊天及运行数据。跨机不并发运行MC，换机先退出等同步；默认不创建旧包备份。

既有Codex审批66项及2026-10-02通知/时间验证见本仓库`_bmad-output/implementation-artifacts/`原报告，只证明相应版本。宽失败见[历史基线](non-live-regression-baseline-2026-09-06.md)，早期背景见[归档交接](archive/entry-context-2026-09-29/handoff.md)。
