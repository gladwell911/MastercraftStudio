# 当前交接

截至 2026-10-01。当前为 `epic5-desktop-navigation` 交付树，测试基线 HEAD `8bc247d8954a4d0bcab24254a53da845f5847f0f`；手机对应 `D:/code/sj/.build-work/epic5/rc` HEAD `19cfe57182fe2b67857ed628c20a7bf623211a0f`。不能用 `D:/code/sj/mc` 主工作树代替本交付。

## 本轮验证（2026-10-01）

五 Epic 核心桌面/模拟器范围通过，由 engineer 独立核对；不是完整产品套件。测试未修改产品代码，不自动改变 Epic4/5 用户验收、合并和发布状态。

| 范围 | 最终结果 |
|---|---|
| Epic1 | 原生快捷键/真实10秒timer 10项，归属与用量UI 55项通过 |
| Epic3/5 | 原生恢复3项、继续与排序28项通过，原严格前台/焦点/键盘断言保留 |
| Epic4 | 桌面展示10项；Android累计299/300秒、未知、跨日、实际“更多”分页3场景通过 |
| 跨端/通知 | Local双provider、私有NATS通知实际点击通过；独立安全锁屏可视通过 |

通知传输/点击与锁屏 production-builder fixture 是独立场景；实际锁屏为两个准确标题各两条和“新消息”，无私密正文。完整命令、初失败、截图、清理证据在 `D:/code/sj/_bmad-output/implementation-artifacts/tests/test-summary.md`，非 Git 工作区产物不随仓库提交。

仍未验证生产 Live、真实模型服务、实体机 TalkBack/震动/后台全部行为、重新打包和双机；旧宽套件 voice/Kimi 精确失败仍按历史计划复核，COM日志限制保留。模拟器 QA PIN/通知清除，隐私设置恢复1/1，showing=false/secure=false；owned进程与adb reverse已清，个人数据库未改变。

## 复现桌面测试

本仓库 cwd，使用已有 Python3.11 `D:/code/sj/mc/.venv/Scripts/python.exe`：

```powershell
$env:PYTHONPATH=(Join-Path (Get-Location) 'tests')
python -m pytest -p owned_window_qa tests/test_model_session_recovery_ui_automation.py tests/test_desktop_navigation_ui_automation.py -q -s
python -m pytest tests/test_epic1_native_e2e.py -q -s
python -m pytest tests/test_chat_information_ui_automation.py -q
python -m pytest tests/test_answer_presentation_ui_automation.py -q
```

helper `tests/owned_window_qa.py` 为已验证源文件的逐字节副本，仅准备测试窗口前台，不替换原键盘或断言。wx GUI 串行；测试隔离 app/notes 数据。不要只反复 Raise 或绕过前台检查。

## 后续与有效背景

- Epic1 聊天信息、Epic2 通知事实、Epic3 恢复、Epic4 回答展示、Epic5 继续与排序已有实现；详细历史基线和 deferred 项见工作区相应 `plan-epic*`/`qa-*`/`review-*`。本轮未合并或发布。
- 源码笔记为 `D:/code/note/notes.db`；打包版要求个人 OneDrive 下 `code/data/sj/notes.db` 已存在并通过完整性/表结构校验，常用命令同目录 `common_commands.json`。旧安装仍写源库，2026-09-29 云端备份仅是快照。切包前从最新源库一致性备份并安全替换或合并；其他电脑独有笔记先导出，一次只运行一台 MC，换机先退出并等同步。
- 发布前按实际范围接续 Live、真实模型、实体设备和新包双机验证，不重复已有效定向测试。
- Git 收尾：本分支无上游，不能推送；RC 测试树 detached HEAD，改动待明确分支处理。未自动调整 remote/上游或主树；主树已有 ASR 修改保持原状。

[历史快照](archive/entry-context-2026-09-29/handoff.md)只证明当时版本。
