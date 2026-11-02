---
title: '修复空白DOMAIN使旧入口迁移失效'
type: 'bugfix'
ticket: ''
created: '2026-11-02'
status: 'built'
route: 'oneshot'
route_source: 'auto'
review: 'quick'
review_source: 'auto'
lenses_ran: ['quick']
review_loop_iteration: 0
followup_review_recommended: false
context:
  - 'D:/code/sj/.build-work/desktop-file-route-20261102/AGENTS.md'
  - 'D:/code/sj/.diagnostics/dog-review-migration-20261102/report.md'
warnings: []
deferred: []
baseline_revision: 'ea241a5d6772ff1a2a5e6bb7afaf19707a96ac24'
---

<intent-contract>

## Intent

**Problem:** 新Dog四视角审查确认P1：空白的REMOTE_CONTROL_DOMAIN或CLAUDECODE_REMOTE_CONTROL_DOMAIN会在机器配置加载时遮盖持久地址，导致default/external旧入口迁移未执行，实际初始化随后又回退到旧域。

**Approach:** 仅对DOMAIN使用运行时一致的有效值优先级：跳过空白主变量、允许非空兼容别名、均空则使用持久值或默认值，再走既有规范化和迁移。TOKEN的显式空值清除语义不变，pair和自定义域保持。补充两别名空值/空白、主空兼容非空、二者非空优先级及正常ChatFrame初始化写入机器配置和app_state/生成文件origin验证。只修确认项；日常MC、凭据、配置、手机及未提交文档不动，不打包、不推送。

</intent-contract>

## Implementation Notes

- 复用原load_machine_config.setting和既有原子写入，不新增公开API；预计生产代码不到10行，总改动不超过100行，oneshot。
- 审查来源P1见context。D1 token-only app_state同步、D2并发replace WinError5为已存在问题，留在审查报告，不混入修复。
- 修复所在隔离分支fix/desktop-route-review-20261102，原主树两个未提交文档保留。Given空DOMAIN和旧default/external配置，when启动，then机器及app_state、消息和文件origin使用新8443入口且token保留；Given空主DOMAIN及非空alias，when加载，then采用alias，不迁移自定义域；GivenTOKEN为空，when加载，then仍清空TOKEN。
- 实现仅DOMAIN遇空值continue，TOKEN保持原按键存在取值；无需main.py业务改动。新增回归选择先red 12 failed/15 passed，补丁后27 passed。扩大到完整配置（排除Dog已复现既有overlapping用例）及真实frame启动/现有地址规范化47 passed/1 deselected（3.15s）。

## Plan Change Log

## Review Triage Log

### 2026-11-02 — Review pass
- verdicts: 0 findings — high 0, medium 0, low 0, false 0, maybe-false 0
- quick独立审查未发现本次diff引入的缺陷、未满足验收条件或违反项目规则的问题。无需补丁、defer或拒绝条目。

## Verification

- 使用D:/code/sj/mc/.venv/Scripts/python.exe，在隔离树对新增回归先red后green；运行配置全文件和相关真实frame启动定向测试。
- 不更改下载实现，复用先前实体97MB三份SHA256一致证据，但不将旧验收宣称为本补丁的部署验收。扩展选择遇既有并发替换失败时记录并区分，不改断言或并发行为。
- 实际命令：`D:/code/sj/mc/.venv/Scripts/python.exe -m pytest tests/test_remote_machine_config.py tests/test_main_remote_nats_unit.py::test_external_desktop_startup_migrates_machine_and_file_origin tests/test_main_remote_nats_unit.py::test_laptop_machine_settings_reach_actual_transport_startup tests/test_main_unit.py::test_remote_nats_defaults_to_fixed_domain_when_host_is_unset tests/test_main_unit.py::test_remote_nats_url_strips_invalid_port_zero_from_domain tests/test_main_unit.py::test_remote_control_settings_normalize_dirty_domain tests/test_main_unit.py::test_fixed_domain_mode_forces_public_host_and_stable_port -k 'not overlapping' -q`。已知并发replace问题未隐藏为全套通过，Dog原审查报告有失败记录。

## Auto Run Result

- Status: built。P1已修复：DOMAIN空值与空白不遮盖兼容别名或持久配置，旧default/external入口迁移恢复；TOKEN显式清空行为保持。
- remote_machine_config.py：仅DOMAIN的有效环境取值分支，未新增公开API；tests/test_remote_machine_config.py：空白别名/优先级/显式TOKEN清空；tests/test_main_remote_nats_unit.py：真实frame初始化同步机器配置、app_state和新origin。
- 原迁移实施计划追加Dog Review Patch记录；本计划保留验证与本轮quick审查结论。
- 新增选择red12 failed/15 passed→green27 passed；扩展47 passed/1 deselected。已知Windows并发replace用例按既有审查记录排除，不声称全套绿。
- 本轮quick 0 findings；patched counts high0/medium0/low0，followup_review_recommended=false。原Dog审查P1为已完成修复的输入，D1/D2为既有问题，仍保留在其报告和原计划记录。
- 日常MC、配置及实体手机未操作，补丁未部署；主树原有未提交文档保留。未推送。此前实体97MB哈希验收不代表本补丁安装验收。
