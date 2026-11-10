# 语音本地先录：新 dog 独立审查

2026-11-10。审查范围 `98016ccdb47f629888d8226900bbcc8272b0df1b..2a089f22f459b4ed465f4d83195c98645e57977c`；启动时 HEAD 为后者、工作树干净。12 文件，+1580/-297。读取全局/项目 AGENTS、handoff、experience、reflection、docs/README、实施计划及其两个 context、独立验证报告。bmad-code-review renderer 在原工作目录调用一次成功，按生成 workflow 执行；已有明确审查授权，不重复请求范围确认。只审查，不修改产品、不打包、不重启日常 MC、不写用户数据。

审查时结论：**0 decision-needed，1 patch，5 defer，2 rejected**。未确认新的产品代码缺陷；确认一项中等优先级回归验证缺口。不能称完整 clean review：edge-case-hunter 返回空数组，按 workflow 记为 failed layer。其余三层均返回有效非空结果。V1后续已补强并独立验证，见末尾完成状态；以下保留原审查裁决与当时证据。

## 独立性与执行

本轮 dog 为新上下文；另逐个启动四个全新、context-free、同能力 reviewer：blind、edge、verification、intent，上一位完成才启动下一位。没有复用此前 dog 或把此前四 lens 当成本轮结果。平台本轮未阻止派遣。blind 的公式为 105982/1024=103.50kB，N=min(floor(sqrt(103.50)+1),10)=10；它实际只提出6项，未凑数，该数量不合其提示要求，结论以实际返回内容和下列核实为限。

## 每项裁决（先独立裁决，再合并）

| ID / 来源 | 裁决 | 已核实证据与处置 |
| --- | --- | --- |
| B1 blind：设备 stop/close 抛错但未释放仍回 idle | maybe-false | `speech_input.py:265` 确实在异常后置 audio_done；但现有 fake 明确先释放再抛错，不能证明真实驱动未释放。已安装 sounddevice 0.5.5 的 stop/close 默认 ignore_errors=True，close 在调用 PortAudio 后置指针NULL；人工替身抛错不等于真实驱动可达缺陷。defer（medium unverified），需实际失败/未释放证据再决定设备不可用策略。 |
| B2 blind：drain 应从 seal 起计，连接等待不应额外加预算 | false | 规范明确为“已连接且 FIFO 已 seal 后”12秒，另保留原10秒连接预算；实现 sender 看到 seal 起算符合契约。拒绝。 |
| B3 blind：wait_for 不是对取消抵抗者的硬清理上限 | maybe-false | asyncio wait_for 会等待取消结算，但此前只在抑制取消 fake 复现；实际 aiohttp 等效失败未证实。本轮未操作真实网络。defer（medium unverified），需真实 transport 复现与资源状态。 |
| B4 blind：声学 ready P95 未验收 | maybe-false | 硬件报告只到ready回调，受控测试替换播放。文档已明确未验收，不构成虚报。defer（medium unverified），需20次物理键到对齐声学出声证据。 |
| B5 blind：真实识别句首未验证 | maybe-false | fixture 固定文本不从PCM识别；字节序验证不能替代真实识别。文档已披露。defer（medium unverified），需隔离真实云端慢连接句首验收。 |
| B6 blind：原小时级问题未实测 | maybe-false | 100周期与短硬件测量不能证明4–8小时；原机制也未证实。defer（medium unverified），需长时同组复测。 |
| V1 verification：控制器多次累计文本回归缺口 | medium | 旧集成测试的多次累计更新被删除；`tests/test_voice_input_integration.py:56` 每代仅收到一个文本；`tests/voice_fakes.py:88` 仅构造final。receiver多包单测只检查回调列表，绕过控制器 `_stream_text`。改成拼接或仅保留首版，现有测试仍可通过。按该lens的preverified证据规则接受，patch，仅补测试。 |
| I1 intent：starting阶段stop取消与early-stop语义 | false | 两种语言读法中，规范状态表已明确 starting=取消、recording且未连接=保留/finish；实现与规范一致。拒绝。 |
| I2 intent：清理是合作式deadline、驱动可能阻塞 | maybe-false | 对驱动无条件回收没有证据，计划已披露；与B1/B3对应边界合并，不额外要求进程隔离。 |
| I3 intent：软件测试与物理/cloud/长稳表面不同 | maybe-false | 与B4/B5/B6相同且已披露的三项现场证据边界；分别合并，非新增完成声明错误。 |

## 最小修复与可执行判据

仅修 V1：扩展现有协议 fake（默认行为保持原样），允许同一代次在最终响应前发送至少两个不同的累计 interim 文本；扩展 `test_latest_text_and_identical_utterances_in_distinct_generations`，让每代经过真实 receiver→controller `_stream_text`→`_converge`，断言只有一次结果且恰为最后完整文本，既无累计重复也非最初片段。继续断言两代相同最终话语各插入一次，覆盖 direct/optimize；无需改产品逻辑。

建议变异判据：临时在隔离副本将 `_stream_text` 改成追加文本、或只存首个非空文本时，该定向测试应失败；恢复原实现后通过。不得在主树留下变异。

验证只需新增定向案例和核心三文件；测试fixture变化若触及既有wx调用方式，再运行已有9项wx定向选择。不扩跑已核实的26项无关旧wx失败，不做实际云端、声学或长稳测试来完成这个局部补测。

## 本轮重新执行的证据

- `.venv/Scripts/python.exe -m pytest tests/test_voice_controller_unit.py tests/test_realtime_asr_unit.py tests/test_voice_input_integration.py -q`：41 passed，1.22s。
- `.venv/Scripts/python.exe -m pytest tests/test_main_unit.py -k voice -q`：17 passed，982 deselected，8.82s。
- `.venv/Scripts/python.exe -m pytest tests/test_voice_input_ui_automation.py tests/test_voice_input_e2e.py -k "local_ready or queued_ready or optimize_worker or software_ready or actual_capture" -q`：9 passed，37 deselected，2.51s；串行执行。
- `git diff --check`：通过。测试进程均退出；fixture完成自身shutdown/资源检查。

这些结果证明本轮指定软件边界，不证明声学出声、真实识别或4–8小时。没有新增后台服务、工作树、数据库或录音；本轮仅创建可复现diff/claims临时目录及渲染缓存，最终审查结论保存在本文件和计划中。

## V1后续完成状态（2026-11-10）

`22284df21b428800384d32de74450a4980c93ad4` 仅补测试，产品逻辑未变。默认fake仍只发final；opt-in每代两个不同累计interim后接不同final，经真实receiver/controller/converge只输出完整final一次，direct/optimize与两代相同final各一次均覆盖。隔离进程内拼接/仅存首版变异各使两个模式在最终结果断言处失败，未在主树留变异；原实现定向2 passed。president quick审查0项问题，核心41 passed、共享fixture串行wx选择9 passed/37 deselected。

engineer独立运行 `.venv/Scripts/python.exe -m pytest tests/test_voice_input_integration.py::test_latest_text_and_identical_utterances_in_distinct_generations -q`：2 passed（0.06s），并核对diff、变异设计与默认fixture不变，复用未变产品代码的核心41/main voice17/wx9证据。V1已修复；原5个defer和审查层执行局限未改变。详见 [局部built计划](plan-v1-voice-cumulative-transcript-regression.md)。
