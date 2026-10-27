# 项目指令

每次开始新的 Codex 项目会话，先读取 docs/handoff.md、docs/experience.md 和 docs/reflection.md；再按任务读取 docs/README.md。历史快照与冻结基线只证明对应版本。

- 明确要求打开 HTTP(S) 网页并显示前台时，使用随源码/onedir 包提供的 `mc-browser-open` 技能；源码入口 `.venv/Scripts/python.exe browser_open.py --url <URL>`，随包入口 `mc_worker.exe --browser-open --url <URL>`，运行时准确 argv 见 `MC_BROWSER_OPEN_ARGV`。普通搜索不启 Chrome；冲突/headless/真实审核拒绝立即停止。默认审批为 auto-review（on-request / danger-full-access / auto_review），访问权限独立保存，缺失或无效时默认完全访问，已有有效手选保持不变。旧无版本审批配置仅迁移一次。跨机首启及焦点效果须在目标机验收。

- 长期维护以 docs/runtime-maintenance.md 为准：保持发布水位/权威facts、用户历史和Codex会话；修改维护时通过真实worker/管道/复制边界验证，不将每批有界或源码验证宣称为存储总上限、月级稳定或安装完成。
- 跨端通知排查先核实际前台 Android 包名、前后台端点及运行 MC；独立 QA 应用可能与主应用同名。测试结束清理自建测试包、进程和转发，并打开日常主包；不清日常数据，不仅凭图标或 ACK 判定验收。

- 本项目是面向读屏用户的 wxPython 桌面应用。没有可见状态变化时，不在后台轮询中触发 UI 重绘、选中变化或状态写入；保持键盘焦点稳定。
- 双电脑远程身份固定为台式机 default、笔记本 laptop；已知计算机名 emperorComputer、emperorLaptop 分别自动识别这两个身份，显式 pair 环境变量优先。配置、命令、文件与已读保持来源归属，笔记本不继承台式机凭据。配置入口见 docs/remote-control.md；本功能不修改 OneDrive 同步。当前日常 MC 由用户本人退出/重启，agent 不主动操作；打包脚本的既有最终自动启动行为仍保留。
- 本阶段审查按用户要求逐个派遣，上一位完成后再派下一位，最后汇总；面向用户使用中文。
- 重新打包 MC 时默认不备份旧程序包，不创建 `mc-before-repack-*` 等旧包副本；只有用户明确要求时才备份。使用 `package_mc.ps1` 先在临时目录完成构建和必要校验，再更新 `D:\code\cx\mc`，保留现有笔记、聊天历史及其他运行数据。最终产物校验通过后自动启动一次；清理 reparse 只操作链接节点，不遍历外链目标。用户要求只修改/测试时不得执行真实打包或启动。
- Codex 私有 home 通过整目录链接复用全局 skills/plugins，新任务刷新原生清单，活动 turn 的 steer 不重启。不得沿链接改写全局技能文件或退回长期副本；来源与验证边界见 docs/handoff.md。
- UI 变更至少运行相关 tests/test_*ui_automation.py 和受影响模型流程测试。wx GUI 套件串行运行；真实定时器从 frame 构造到销毁全程清理。
- CLI 后续输入、异步状态及结果必须按 chat、turn、session、account 和 request generation 归属；按规范化 model id 分派 provider。执行页保持 owner、revision 与有界前台读取，重工作放后台。
- Kimi REST 回答、思考、分页与旧 owner 恢复共用结构化 prompt 边界判定；仅明确标记的 injection 可跳过。metadata.origin 的优先级与未知值保守规则见 docs/README.md，不能按消息正文识别注入。
- 源码笔记路径为 D:\code\note\notes.db；打包版需要现存且经完整性校验的个人 OneDrive\code\data\sj\notes.db，缺失时在初始化前停止。测试须 monkeypatch resolve_notes_data_dir()。
- 切包门禁：2026-09-29 的 OneDrive 数据库只是一次快照，早期安装包曾写本地源库。新包启用前核对实际运行包的数据路径，补齐之后的改动并安全替换或合并。两台 MC 可保持远程连接，用户保证不同时写入共享数据；换机写入前等同步。当前步骤见 docs/handoff.md。

原指令细节见 [归档](docs/archive/entry-context-2026-09-29/AGENTS.md)。
