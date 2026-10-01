# 五 Epic 深度代码审查 — 2026-10-01

审查完成：0 decision-needed，8 patch，3 defer，6 rejected。审查阶段没有修改产品代码。
范围：MC d47e11fa7c3498da8be1db157a7745206d3bcb84..67f7c6a；RC a01531098a7fcf8f62d626782e92485073bd0204..7b15ecd。53 文件，新增5095/删除221行，diff 380801 bytes。
五 Epic 计划及 SPEC/CAP-1..12、两仓 AGENTS/当前交接均纳入；RC 既有 ASR 未提交改动排除。
技能：bmad-code-review render 成功，thorough/full，单轮。四 lens 均完成。受总线程上限限制，blind/edge 为新独立线程，verification/intent 复用两个已完成线程并要求忽略先前结论；后两 lens 上下文独立性受限。未把历史 QA 当本轮审查结果。
用户已明确授权 director 修复；不重复技能中的确认问题。后续仅修 patch，engineer 独立验证实际影响。

## 给 director 的修复清单

| ID | 严重度 | 位置 | 已确认影响、最小修复及验证 |
|---|---|---|---|
| R1 | medium | mc/main.py:633，20010 | HTML有序列表 start 非整数或无值会使详情转换抛 ValueError/TypeError，转换位于窗口清理 try 前。安全解析序号，异常默认1，确保转换失败不泄漏窗口。验证真实详情打开、正常序号与非法/无值属性、关闭还原canonical。 |
| R2 | medium | mc/main.py:623 | 链接目标被删除：[documentation](https://example.com/docs) → documentation。详情纯文本成为唯一可编辑/复制内容后地址不可用。保留非冗余链接目标；URL本身已作为链接文字时不重复。正文/canonical/scratch约束保持。 |
| R3 | medium | mc/main.py:629 | first<br><br>second → first\\nsecond，明确空行丢失。br 每次追加换行，而非最小边界。验证连续br及普通段落，保持代码块字面内容。 |
| R4 | medium | mc/main.py:628 | - outer / 缩进 - inner / - next 被转换为无标记、无缩进三行，父子关系丢失。按已有列表栈保留最小缩进/层级，勿重写Markdown系统。验证嵌套有序/无序及文字顺序。 |
| R5 | low | mc/main.py:631 | 两列表格输出 a\\tb\\n\\tc\\td，第二行第一列凭空右移。分隔符按当前行的列位置插入。直接局部修正并检查真实详情文本即可。 |
| R6 | medium | mc/main.py:612，1185 | 每个boundary拼接累计全文，详情在wx线程同步转换，长回答平方增长并冻结键盘。只维护输出末尾换行数或等效局部状态，避免重复join。review本机2000/4000/8000短段（38/76/152KB）0.078/0.228/0.724秒；blind较长段0.411/1.271/5.787秒。修复后相同输入对比并跑相关UI，不增加全系统性能工程。 |
| R7 | medium | mc/main.py:2655，2791 | 完整刷新与context-only共享pending；60秒完整timer遇到仍在途的context请求直接返回，完成后不补发。50秒请求持续11秒时60秒完整刷新消失；重复这种相位会持续失去完整刷新。保留有界去重，记录一次待完整刷新，当前context请求完成后在相同dialog/identity有效时补发完整读取；关闭/换owner不能补发旧查询。Codex/Kimi分别验证慢context→完整timer→完成→恰好一次完整读取。 |
| R8 | medium | mc/codex_worker_client.py:143；tests/test_codex_worker_client.py:83 | context_only真实IPC到worker消费者的测试断裂：timer替换请求入口、worker测试手造正确字段，实际client序列化只测默认值。删除/固定False字段可逃逸，使每10秒请求变完整额度请求。与R7必要检查一并补实际client发送True字段→worker处理的定向验证，确认只查上下文、不查账号/额度。 |

范围只涉及 MC；未确认本轮 RC 产品缺陷。R1–R6集中于同一新纯文本投影，但各自根因不同，不能以改同函数就吞掉独立验收。
推荐定向验证：详情相关 test_answer_presentation_ui_automation.py；信息请求相关 test_chat_information_ui_automation.py / test_codex_worker_client.py / test_codex_worker_process.py；wx GUI串行。不重跑全部五Epic或生产Live。

## 每个原始 finding 的 verdict（先判定，后分组）

| 原始ID/来源 | verdict | 证据及路线 |
|---|---|---|
| B1 blind | medium | AST隔离执行生产转换函数，非法start直接ValueError；patch R1。 |
| B2 blind | medium | 实际函数删除URL目标，违背保留回答内容的详情边界；patch R2。 |
| B3 blind | medium | 两个br只剩一个换行；patch R3。 |
| B4 blind | medium | 实际嵌套列表丢层级；patch R4。 |
| B5 blind | low | 实际第二表格行首多tab，直接修正；patch R5。 |
| B6 blind | medium | 同输入规模实测非线性，调用链为GUI同步；patch R6。 |
| B7 blind | medium | 两provider入口均直接丢弃仍在途期间的完整请求，无补发分支；patch R7。 |
| B8 blind | maybe-false | token_count部分记录合法测试存在，但未证明真实provider会在完整记录后以部分记录覆盖最新有效项。medium-unverified defer D1。不能自行把旧上下文当新观测。 |
| B9 blind | maybe-false | 非单调时间展示行为属于未指定输入规则；未证实违约，若成立仅低影响，reject。 |
| B10 blind | maybe-false | post后的cancel是否会发出瞬时音/震动未做设备实证，且actor竞态机制不是本轮新增；medium-unverified defer D2。 |
| E1 edge | false | 初次AST注入ACK前metadata不可达；唯一metadata写入来自ACK，真实ACK前exit在新guard之前返回。撤回新增guard回归，既有ACK前exit终态缺口单列D3，不交director。 |
| E2 edge | medium | 与B1同一start解析异常；patch R1。 |
| V1 verification | medium | 实际IPC标志删除可逃逸既有tests，需真实消费链路；patch R8。 |
| V2 verification | low | 已有Kimi成功/replay活动与失败status/无完成音测试，缺失败/interrupted排序断言；没有发现当前错误活动调用。纯回归加固，本轮用户要求适度验证，记录而不扩大修复，reject。 |
| I1 intent | false | 通知标题生成时冻结符合重放不可变契约；新回答使用新title，无违约。 |
| I2 intent | false | RC _buildMessagesFromRemoteTurns给同一回合question和answer相同turn.createdAt（main.dart:4861、4874）；可信时间下第二条不会新增时间。不能仅以消息/回合输入单位不同认定两端不一致。未知时间每条显示差异为既有表示，不足以归因本轮。 |
| I3 intent | false | 10秒timer尝试且在途合并是有界防重；未完成请求不应堆积。该读法本身无错误，完整刷新被吞的问题已独立R7。 |

## Deferred（禁止本轮扩展修复）

- D1：mc/codex_client.py:641 原生token记录部分字段回退候选。需要真实或项目规范允许的 complete→partial 序列，证明最新上下文确实无法恢复，再决定分别采样和观测时间语义。
- D2：RC RemoteBackgroundNotificationActor.kt:257 post→gate变化→cancel的瞬时提醒。需设备真实音/震动/heads-up实证；既有机制，不改通知架构。
- D3：MC main.py:15893 ACK前worker exit没有metadata，可能留下pending。需真实受控请求发送后ACK前终止worker并观察完整timeout/recovery，建立既有缺口及用户影响。不能注入不可达metadata，也不放宽旧generation防护。

## Rejected appendix

B9 maybe-false：时钟回拨展示规则未指定，低影响推测不扩展。
E1 false：注入ACK前metadata无真实写入路径，新guard回归结论撤回。
V2 low：纯Kimi失败排序测试加固，无产品错误，非本次必需验证。
I1 false：标题冻结符合不可变事实契约。
I2 false：生产映射同一turn统一timestamp，输入单位差异不能证明实际可信时间违约。
I3 false：合并在途context请求本身正确；R7处理独立完整刷新丢失。

计数：8 patch条目来自9条存活原始发现（R1合并B1/E2）；3defer条目含2候选及1由已撤回E1中识别的既有缺口；6原始 rejected。
