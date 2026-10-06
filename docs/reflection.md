# 纠错反思

## 2026-10-06 全局技能与打包纠正

| 修改内容 | 错误归因 | 下次指令建议 |
|---|---|---|
| 旧 reader 加入进程身份守卫后，停止进程不再通过 reader finally 结束 pending 请求；补为停止时显式失败所有旧等待者，并验证过期 reader 不影响新进程 | 判断逻辑有问题 | 修改生命周期守卫时同时核对等待者的结束路径，用实际阻塞请求验证立即唤醒，不能只检查进程已退出。 |
| 原生探针起初把临时环境变量视为完全隔离，且按探针名过滤结果漏报真实 `.agents` 元数据读取；补查全部技能路径并修正验证边界 | 判断逻辑有问题 | Windows 隔离验证同时检查 KnownFolder 与完整返回路径；临时认证/写入隔离和真实技能只读发现分别陈述。 |
| 初始同名技能比较使用了不同 extraRoots 配置；改为两侧相同配置后核对记录及顺序 | 判断逻辑有问题 | 先固定影响发现的配置，再比较全局与私有 home；原生发现结果不等于模型最终同名选择已验证。 |

## 2026-10-05 已读同步纠正

| 修改内容 | 错误归因 | 下次指令建议 |
|---|---|---|
| wx RegisterHotKey 静默丢弃 MOD_NOREPEAT，真实长按验证后改为原生注册 | 判断逻辑有问题 | 核对包装器实际转换，再用真实重复 keydown、释放及快速连续按键验证；不能以传入标志证明生效。 |
| 热键逆序取尾部回答可能漏掉较早回合的更高完成序列，审查后修为冻结范围内最大可见序列 | 判断逻辑有问题 | 用逆序完成与隐藏历史边界测试确认序列，不把显示尾项当完成顺序上限；焦点仍保持实际尾项。 |

## 2026-10-02 本轮纠正

| 修改内容 | 错误归因 | 下次指令建议 |
|---|---|---|
| 初版只验证时间字段与重建，遗漏已有流式回答完成和 accepted 延迟刷新 | 判断逻辑有问题 | 先建立实际已有回答行，再经过 provider 终结与 quiet/deadline，跨 300 秒后检查可见时间行位置、选中及焦点。 |
| 仅修正 startup 文案后遗漏共享预算中后续 owner 的无错误兜底，独立验证后补修 | 判断逻辑有问题 | 截止时间改动覆盖入口过期、退避后过期、串行 owner 继承过期预算；真实错误优先。 |
| 初版 F1 切换仍取消已准备缓存，GUI 回归后修正 | 判断逻辑有问题 | 显示模式切换与数据失效分开，用隐藏预备后首次 F1 无额外读取验收。 |

## 2026-10-01 深审判断纠正

| 修改内容 | 错误归因 | 下次指令建议 |
|---|---|---|
| 撤回“ACK 前新 generation 守卫导致请求卡住”的结论，候选留作既有行为验证 | 判断逻辑有问题 | 人工构造异步状态前，先证明每个字段的真实写入路径和事件顺序；ACK 才写入的 metadata 不可注入 ACK 前场景作为产品回归证据。 |

- Epic5: archived provisional output and Kimi startup/approval state must not advance recency. Preserve active pinned state during slim persistence and mark accepted legacy worker turns done to prevent repeated activity. Formal review caught unsuccessful Codex terminals mapped as `turn_completed`, OpenClaw visible sync bypassing history dirty, and Kimi's weak `>=` receipt assertion; all three repair groups are closed after protocol/surface tests. Initial native failures were focus/empty-owner/arrow-injection preconditions, retained as history rather than inferred product shortcut failures.

实际错误形成的下次检查点：

- 旧交接和票据不能单独证明 Story 完成；核对代码、提交、状态及测试。Live 验收需证明真实生产调用链和有效结果。
- 用户说“改成 X 那样”时先核对 X 的真实样例；菜单入口沿实际按键绑定验证可达性。
- 远程执行投影曾阻塞 wx 主线程，空活动轮曾误用上一轮末页；先明确线程边界，所有守卫和查询按目标 turn 过滤。
- 恢复 worker 不一定在健康活动轮中执行；设计增量显示前检查实际触发路径与退出条件。
- 发布通知不得把请求占位当最终回答；只从持久化 done turn 生成。跨端证据须明确是否来自同一次最终树。
- 终答补发队列曾在读取持久化状态和投递前清空，瞬时失败会丢失补发。下次只在通知及历史入队成功后移除单项，并用失败后重试测试确认。
- 清除成功不能仅凭修订号判断：相同修订可能来自无关操作。下次同时核对操作 ID、修订和权威清除状态。
- 真机安装保留数据时，不用会隐式卸载的命令；新包切换前核对 OneDrive 笔记快照之后的源库变化。
- 测试失败先按精确用例区分产品回归、旧预期和环境差异；不能用宽泛筛选器得出全量结论。
- 首版云端笔记库检查只要求 SQLite 完整且有表，可能把无关数据库当成笔记库；后续改为校验当前及旧版笔记表结构。验证“不会创建空库”时，应测试一个结构完整但用途错误的数据库，并断言文件未改。
- 首次在线备份完成后未显式关闭 SQLite 临时连接，Windows 拒绝重命名；下次使用 backup API 时显式关闭源和目标连接再放置文件。首次临时文件清理由自动审批以策略限制拒绝，未绕过。

完整纠错记录见 [归档原文](archive/entry-context-2026-09-29/reflection.md)。
- Recovery QA needs the complete authoritative final-answer protocol; a delta is insufficient. Native Alt+A automatically resends the first question, so a cleared empty-turn expectation is wrong. Activate the native GUI event loop and await visible answer rows after the existing navigation quiet interval; done status alone is not visible-answer evidence. Record the configured startup attempt budget, actual child exit and final request state.


The final combined review found two asynchronous identity gaps missed by the initial recovery scenarios. Exception/overflow prior-owner regressions and paired retired/current client UI tests now cover both; the focused dog follow-up checks the repaired boundaries without restarting the entire review.

## 2026-10-01 用户纠正

| 修改内容 | 错误归因 | 下次指令建议 |
|---|---|---|
| 首轮因原生前台阻塞过早结束，未完成五 Epic 覆盖，用户要求继续后才补齐 | 判断逻辑有问题 | 按逐 Epic 场景矩阵推进；环境失败先执行最小恢复方案，不以部分通过结束整个任务。 |
| “测试完成”容易被理解为完整产品套件通过 | 判断逻辑有问题 | 明确本轮核心范围与未执行项；受控模型、模拟器、独立锁屏、生产 Live 和实体机分别表述。 |

## 2026-10-04 本轮纠正

| 修正内容 | 错误归因 | 下次检查点 |
|---|---|---|
| 原始尾部 LIMIT 被隐藏步骤占满，旧可见步骤消失 | 判断逻辑有问题 | 用真实步骤加大量隐藏后缀验证冷打开。 |
| SQL Python 判定函数虽有 LIMIT 仍扫描大量隐藏行 | 判断逻辑有问题 | 持久化 visible 并建部分索引，核对扫描工作量。 |
| Session 发布后才修改代理配置 | 判断逻辑有问题 | 完成 trust_env 配置后再共享。 |
