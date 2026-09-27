---
title: '接入 Kimi 额度及可见窗口刷新'
type: 'feature'
ticket: '4'
created: '2026-09-27'
status: 'built'
route: 'oneshot'
route_source: 'auto'
review: 'quick'
review_source: 'auto'
---

<intent-contract>

## Intent

在聊天信息窗口显示 Kimi OAuth 五小时及七日额度。窗口可见时低频重查 Codex 和 Kimi 信息；异步结果需匹配聊天、原生会话、账号和请求代次，且无变化时不重绘列表。

</intent-contract>

## Implementation Notes

- Kimi 客户端读取 `GET /api/v1/oauth/usage`；`kind=error` 与请求失败均显示查询失败。
- 五小时额度显示已用百分比及本地绝对重置时间；七日额度显示已用百分比及按当前时刻计算的剩余时长。两个窗口分别处理缺失字段。
- Kimi 额度请求独立于 status/snapshot，可在尚无 session 时查询；窗口计时器每 60 秒只重查额度并重新计算七日倒计时，不周期性请求 session 数据。Codex 额度仍随计时器重查。
- 后台先读 `/api/v1/auth` 的 managed provider 状态，再读 `/api/v1/oauth/userinfo` 的稳定 `userInfo.userId`，然后读额度，并在提交结果前再次确认 userId 和认证状态。额度按返回的账号 ID 缓存；账号改变时清理旧值，显示前检查额度归属与最近确认的账号一致。原生 session 变更时清除旧额度并重拉。请求代次、窗口和聊天/session 身份继续约束结果。服务端没有推送账号变更事件时，外部变更到下一次后台查询之间可能短暂显示带时间戳的缓存。
- 未配置托管 OAuth 显示“不适用”，unauthenticated/expired/revoked 与 HTTP 401 显示“未登录”；其他失败显示“查询失败”。缓存行标记上次更新时间，重新打开窗口不会冒充实时值。
- 仅 HTTP 401 明确显示未登录；当前未确认登录方式不适用的 API 错误码，其他 `kind=error` 显示查询失败。
- 额度文本不变时不调用列表 `Set`，保留选择与焦点。

## Verification

- `tests/test_chat_information_ui_automation.py` 与 `tests/test_kimi_server_client_unit.py`: 104 passed。
- 独立工程验证待完成。
