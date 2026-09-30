# MC Codex 上下文窗口显示：口径、模型归属与修复建议

日期：2026-09-30（Asia/Shanghai）。状态：只读调查完成，未修改产品代码、Codex 配置或生产数据，未发出模型请求。仅新增本分析文件。官方文档通过 OpenAI Docs 路线查询并打开；以下本机数据为调查时快照。

## 结论

**MC 不能把当前会话的分母从 258,400 直接换成 API 标称的 1,050,000。** 已有 Codex rollout 同时记录实际模型 `gpt-6-sol` 和运行时窗口 `258400`，本机目录默认值 `272000 × 95% = 258400` 也完全一致。因此目前分母符合当前 Codex 运行口径；确定的产品错误是把这个容量反推出 `gpt-5-codex`，并混淆“本会话有效窗口”和“模型 API 标称最大窗口”。

扩大 Codex 窗口属于独立的配置变更。官方支持 `model_context_window` 设置，但当前目录的 `max_context_window=872000`、实际客户端版本、模型/账号可用能力都必须纳入验证。当前证据不能保证设置成 1,050,000 后该 Codex 会话真能使用该容量。

## 三种窗口口径

| 口径 | 本次值 | 应如何使用 |
| --- | --- | --- |
| 当前 app-server/rollout 报告的会话窗口 | 258,400 | 当前使用百分比的权威分母；跟随该 thread/turn 的事件更新 |
| 本机 Codex 模型目录默认/最大与保留比例 | 默认 272,000；有效比例 95%；目录最大 872,000 | 解释 Codex 默认行为和候选配置范围，不覆盖已观测到的运行值 |
| GPT-6 Sol 的 API 标称窗口 | 1,050,000 | 独立的模型能力参考信息，注明 API 口径及来源；不是此 Codex 线程已经启用的容量 |

官方 GPT-6 Sol 页面明确列出 1,050,000 上下文窗口和 128,000 最大输出。这是模型 API 页的能力描述。[GPT-6 Sol 模型页](https://developers.openai.com/api/docs/models/gpt-6-sol)

目录里的 872,000 与 API 1,050,000 的差异，在已打开的官方文档中没有给出本版本完整推导。不要自行解释成减去输出预算，也不要把目录最大值当成服务端硬限制已经实测过。`max_context_window` 的确切钳制/覆盖逻辑仍需要与所运行 CLI 版本匹配的实现或离线测试确认。

## 本机非敏感证据

三个配置只读取了 model、窗口、压缩阈值、profile 白名单字段；未读取认证文件内容。

| CODEX_HOME 位置 | model | 顶层 model_context_window / model_auto_compact_token_limit | 缓存版本与时间 |
| --- | --- | --- | --- |
| `D:\code\sj\mc\.codex-home` | gpt-6-sol | 均未设置 | client_version 0.157.1；2026-09-29T18:16:00.881129100Z |
| `D:\code\cx\mc\_internal\.codex-home` | gpt-6-sol | 均未设置 | client_version 0.157.1；2026-09-29T22:57:37.640964700Z |
| `C:\Users\gladwell\.codex` | gpt-6-sol | 均未设置 | client_version 0.155.0；2026-09-29T22:57:32.563934100Z |

三个 `models_cache.json` 的 `gpt-6-sol` 项均为：`context_window=272000`、`effective_context_window_percent=95`、`max_context_window=872000`、`auto_compact_token_limit=null`。缓存中的 client_version 是产生该缓存的元数据，不应直接等同于现在运行进程的二进制版本。

还只读扫描了打包 home 最新三个 rollout 的模型与 token_count 元数据，未输出问题、回复或认证内容：

- `01a0eab2-03f0-78d1-afd0-4a601fbcead3`：turn_context 模型 gpt-6-sol；窗口集合仅 258400；最后观测 used=78983。
- `01a0ec73-5f92-7cf2-832b-922f7645027a`：模型 gpt-6-sol；窗口集合仅 258400；最后观测 used=222320。
- `01a0ecc2-3e24-7613-8231-0a8c40761a6c`：模型 gpt-6-sol；窗口集合仅 258400；最后观测 used=83202。

最后事件时间约为 2026-09-29T22:58–22:59Z。这些证据直接证明 Sol 在当前 Codex 环境可报告 258400，容量到模型名的一对一映射不成立。本次未定位用户截图 124120 对应的精确 turn，不能声称上述三条就是截图同一轮；截图数值应由其原始 owner 事件确认。

## MC 当前数据链与确定缺陷

### 窗口与用量解析

`codex_client.py:1118 codex_context_usage_from_payload()`：

1. 当前上下文用量优先取 `tokenUsage.last.totalTokens`，兼容旧协议 `info.last_token_usage.total_tokens`。
2. 分母取 `tokenUsage.modelContextWindow`，再兼容 `info.model_context_window` 等旧字段。
3. 当前 model 优先级为显式 model → rate_limits.limit_name → 容量推断 → fallback_model。
4. `CODEX_MODEL_BY_CONTEXT_WINDOW` 在文件开头把 258400 固定映射为 gpt-5-codex、121600 映射为 spark。

窗口取 runtime 字段的方式应保留；第 3、4 点需要修复。相同有效窗口可以属于多个模型，也可因配置改变；配额桶 `limit_name` 更不能保证是该 turn 的真实模型名。

`codex_session_total_tokens_from_payload()` 另取 `tokenUsage.total.totalTokens`。累计用量与当前 last 用量必须保持分离；不要把会话累计 token 用作窗口占用分子。上下文压缩后当前量可以下降、累计量继续上升。

### 错误模型优先于配置

`main.py:_active_chat_current_model()`（约 5456）优先返回 pending/context_usage.model，因此错误推断的 gpt-5-codex 会遮住后面的 `read_codex_cli_model_label()` 所读取的 gpt-6-sol。

简单地把配置模型提前同样不准确：恢复的旧 thread、模型重路由、用户随后更改默认配置，都可能与当前轮的真实模型不同。不能把所有历史聊天改标为当前配置的 Sol。

### UI 标签没有说明口径

`main.py:_chat_information_rows()`（约 2382）直接显示“当前上下文：已用 …（used/window token）”；`context_usage.py:format_context_usage_label()` 显示简写。数字没有说明是“本会话 Codex 有效窗口”，用户很容易拿 API 能力表比较。

`context_usage.py:MODEL_CONTEXT_WINDOWS` 还为 `codex/main` 固定 258400。这一表用于估算，不能变成原生 Codex 的权威数据源；未知 runtime 窗口应显示未知，而不是从此表伪造精确窗口，也不能把这张表的 Sol 值直接设成 1.05M 来掩盖运行数据。

## 可否配置更大的窗口

官方配置参考支持 `model_context_window`；`model_auto_compact_token_limit` 是另一个自动压缩触发阈值，未设置时使用模型默认值。官方示例将这些归入可选的手工模型元数据。[配置参考](https://learn.chatgpt.com/docs/config-file/config-reference)、[配置示例](https://learn.chatgpt.com/docs/config-file/config-sample)

因此可以提出“评估扩大”的配置方案，但本次没有修改任何值，也没有验证更大的请求。执行时应：

1. 确认 MC 实际启动的 codex.exe 路径、`--version`、CODEX_HOME、profile 与有效配置来源，记录同一 worker 的事实。不要把不同客户端缓存版本混用。
2. 以本版目录最大 872000 作为候选范围参考，先验证客户端接受/限制规则。若仍按 95% 报告有效窗口，则 **872000 × 95% = 828400** 是候选推算，不是已启用或已验证的实际窗口。
3. 不设置 1,050,000 来追求显示数字相等；API 宣称容量并不证明当前 Codex 适配层启用了它。也不要给已经是有效值的 modelContextWindow 再乘一次 95%。
4. 如调整压缩阈值，独立设定并验证其与有效窗口的关系；保留足够任务/工具/输出空间。不要在 UI 用压缩阈值替代窗口分母。
5. 新配置可能只作用于新启动 worker/新 turn，恢复线程可能沿用已绑定模型。以对应 thread/turn 的后续 tokenUsage 报告为验收事实。
6. 显示修复可以先完成，不依赖实际增加窗口。大窗口配置试验另行授权并隔离运行，防止对现有长任务造成恢复/压缩语义变化。

重要实现细节：`codex_client.py:196 build_codex_app_server_env()` 建立工作目录下 `.codex-home`，`_copy_codex_home_seed()` 会从用户 `.codex` 用 copy2 复制 config.toml、models_cache 等文件；并非“目标不存在才复制”。仅修改打包 `_internal/.codex-home/config.toml` 可能在下一次启动时被用户 home 种子覆盖。配置归属必须先厘清，不能直接在运行包副本随手改值。本次仅检查了顶层白名单字段，未解析所有 profile/项目配置优先级，配置建议不得声称全局有效配置已经穷尽确认。

## 最小修复设计

### 1. 分离路由标识、真实模型与窗口来源

- 保留应用路由 ID `codex/main`，用于 provider 分派，不替换成原生模型 slug。
- 为原生模型保留明确 provenance：例如 `runtime`、`configured`、`legacy_unverified`、`unknown`。不要让 context_usage.exact 同时表示“模型身份可信”；它现在只反映窗口字段是否存在。
- 模型优先级建议：当前 thread/turn 的显式返回或重路由结果 → 同一线程 start/resume 的已验证响应模型 → 本轮启动配置快照（标为配置值） → 未知。
- 删除容量到模型的推断；不要将 rate-limit bucket 名自动当作模型。
- `codex_client.py` 保存按 thread 作用域的解析模型；`codex_worker_process.py` 把该元数据随 owner/turn/context generation 送至 UI。现有 worker payload 的 `model` 多为路由 ID，不能误当 native model。
- `thread/start`、`thread/resume` 的结果目前主要提取 ID，应检查并保存当前 CLI schema 实际提供的解析模型字段。官方文档还定义 `model/rerouted`（fromModel/toModel/threadId/turnId）；绑定到对应轮次，防止变更污染整个历史。[App Server 文档](https://learn.chatgpt.com/docs/app-server)

可用本机 CLI 离线生成与版本一致的 schema，核对字段后实施。官方文档给出 `codex app-server generate-json-schema --out <目录>`；本次未生成或启动 app-server。不要先假定每个老版 response 都有某字段。

### 2. 窗口分母坚持 runtime 优先

- `context_window` 保留现有语义，表示匹配 owner 的运行报告值。
- 可添加向后兼容的可选字段：`window_source`、`window_scope`、`model_source`。如果需要目录能力说明，另存 catalog_default/catalog_max/effective_percent 与缓存版本时间，不混入实时分母。
- 数据匹配至少绑定 chat、thread、turn、context generation、账号/worker 实例或已有等效作用域。旧线程迟到事件不能覆盖新窗口。
- 新线程、清空上下文或切换模型后没有新报告时，显示“窗口暂未知/等待运行报告”；历史最后值可保留，但标为历史，不假装新线程已验证。
- runtime 缺失时可以展示“配置参考窗口”，但不能标 exact，也不能用它生成看似精确的当前占用百分比。

### 3. 推荐显示

本例应显示：

```text
模型：GPT-6 Sol（本会话确认；尚无确认时注明“配置模型”）
当前上下文：124,120 / 258,400 token，48.0%
窗口口径：Codex 本会话有效窗口
```

可在详情中追加“模型 API 标称窗口：1,050,000（能力参考）”；不强制在常驻列表增加多行技术信息。若未来实测报告 828400，则自动显示该实际分母；无需再改模型常量。

不能显示 `124120/1050000 ≈ 11.8%` 来替代当前 48.0%，否则用户对何时压缩的判断会被误导。不要因为 API 最大输出是128000就从 runtime 再扣一次预算；runtime 已给出的分母不应由 UI 重算。

### 4. 旧数据兼容

- 保留旧 used_tokens、context_window 与 source 数值，不批量重写历史分母。
- 旧记录的 gpt-5-codex 可能真实，也可能由容量推断。没有 provenance 不能简单全部替换为 Sol。
- 新记录添加来源/version；读取旧无来源记录时将模型身份视为未验证。若当前 scope 有可靠原生模型，可用于当前显示；若只有当前配置，显示“配置模型”，不能回填成历史事实。
- 官方模型标签与 routing ID 分离，保持 canonical owner、远程协议及已有序列化字段兼容；新增字段缺失时有安全默认。
- `ContextUsage.to_dict()`/`context_usage_from_dict()` 必须同步处理新增字段，否则经过 normalize 或持久化会丢失 provenance。
- UI 和 RC 使用同一语义；修复桌面标签时核对远程状态中 model/context_window 的消费者，不让手机继续按错误模型名呈现。

## 测试与验收

### 纯本地解析/持久化测试

1. `tokenUsage.last.totalTokens=124120`、`modelContextWindow=258400`，显式 model=Sol：分母保持258400，模型Sol，48.0%。
2. 同窗口但无显式模型：不能推断gpt-5-codex；同线程已确认Sol时使用它，没有证据则unknown/configured。
3. 配额桶 limit_name 与真实模型不同：不覆盖真实模型；121600也不能自动确定Spark。
4. 旧 info/snake_case 与新 tokenUsage/camelCase 等价；null/0/缺失窗口返回未知，不从硬编码表冒充真实。
5. 同一模型分别报告258400、828400和其他有效值：逐值保留，不再乘95%，不做API上限替换。
6. last与total不同、压缩后last下降：当前占用用last，累计用total。
7. 两个聊天不同模型/窗口交错事件、旧generation迟到、thread重建：无跨聊天、跨轮覆盖。
8. model/rerouted或恢复旧模型：作用于指定turn；更改全局默认不改写历史身份。
9. 新字段经 worker 协议、ContextUsage、数据库、远程状态往返保持；旧无字段记录安全显示，真实旧gpt-5记录不被武断改名。

### UI/E2E 测试

- 用临时 ChatStore/notes 和受控 app-server fixture，经真实事件解析、worker、UI链发送Sol/258400，检查信息弹窗与列表标签、累计量和百分比；无需真实模型请求。
- 两聊天切换、后台事件、压缩/重启/清空后显示来源正确，保留焦点与列表选择。
- 配置参考/API能力行与当前有效分母明确区分；同值重复事件不重复刷新。
- 如未来获准扩大配置，另做隔离客户端版本配置验收与实际窗口报告观察；本次未进行，不能把828400作为通过结果。

相关文件：`tests/test_codex_client_unit.py`（约543–652已有258400与rate_limits案例）、`tests/test_context_usage_unit.py`、`tests/test_codex_worker_process.py`、`tests/test_main_unit.py`、`tests/test_context_usage_ui_automation.py`、`tests/test_chat_information_ui_automation.py`。旧测试若断言“258400必为gpt-5-codex”，应改为验证显式来源，保留真实旧模型fixture。

建议先增加关键字 `codex_window_identity` 的定向测试，再串行运行相关GUI：

```powershell
Set-Location D:\code\sj\mc
.\.venv\Scripts\python.exe -m pytest tests/test_codex_client_unit.py tests/test_context_usage_unit.py tests/test_codex_worker_process.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_main_unit.py -k codex_window_identity -q
.\.venv\Scripts\python.exe -m pytest tests/test_context_usage_ui_automation.py tests/test_chat_information_ui_automation.py -q
```

GUI测试串行，使用临时笔记路径，不接触生产账号认证内容；必要时补远程协议定向测试。验收必须证明：运行时分母不被篡改、模型不再按容量猜测、来源可信度明确、旧记录不被错误重标、作用域正确。整个显示修复应能在不发送真实模型请求、不修改用户配置的条件下完成。

## 尚未确定的边界

- 没有读取与当前二进制完全一致的 Rust 实现，不能证明 `max_context_window` 的具体钳制算法。
- 未设置更大窗口、未发大型请求，不保证当前账号、服务路径可用828400或1.05M。
- 未定位截图124120对应原始事件，但已独立确认多个真实Sol会话报告258400。
- 官方网页及缓存可能更新；实现应信当前作用域runtime证据，不将本文快照硬编码为长期真理。
