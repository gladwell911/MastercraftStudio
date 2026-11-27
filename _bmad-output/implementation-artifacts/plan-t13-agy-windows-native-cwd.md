---
title: '恢复 Windows 上 AGY 原生历史目录'
type: 'bugfix'
ticket: ''
created: '2026-11-26'
status: 'built'
route: 'oneshot'
route_source: 'auto'
baseline_revision: '15e7faead38d4eb17562f311a5b40bc971af6e96'
review: 'quick'
review_source: 'auto'
lenses_ran: ['quick']
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** AGY 1.5.17 在真实 ACP 清单中用 /D:/… 表示 Windows 项目目录。MC 把它判为非绝对路径，导致原生聊天列表能发现，但详情和续聊无法使用实际项目。
**Approach:** 在 Windows 识别 AGY 返回的盘符前缀，统一使用可由本机文件系统解析的路径。既有存量路径继续可通过同一验证逻辑打开。清单仍可列出失联的项目，真正读历史、发送及验证删除时继续检查目录和来源身份。

</frozen-after-approval>

## Implementation Notes

本轮 T13 桌面自动化在真实 1.5.17 session/list 观测到带额外斜杠的盘符路径。预计修改路径规范化、AGY 清单、续聊启动和现有回归共四个文件。

回归先在基线上复现，带前缀的会话历史缺少分页及正文。保留标准路径原有行为，并使用 AGY 验证函数返回的实际目录启动 ACP。

## Plan Change Log

## Review Triage Log

- Medium，修复了回归缺口。除清单前缀输入外，在发送前把同一档案的持久目录设回旧式写法，核对恢复请求仍使用规范化目录。
- Medium，补齐真实验证。隔离保留的 AGY 1.5.17 二进制仍从清单读到前缀路径，修复后可读取原答并按同一 ID 续聊，真实答复回显此前会话特有标记。测试使用同一 Windows 用户的供应商登录和合成会话。独立证据见工作区 candidate-final/t13-agy-real-restore.log。

## Verification

- 真实原始 ACP 清单报告前缀目录，MC 读取同会话的合成正文，续聊交给原项目目录。
- 用原生历史回归重现此前路径和标准路径两种形式，并复跑 AGY 项目、终端与附件测试。
- AGY 历史、终端、项目、附件和原生删除定向 21 项通过，包含真实 Codex 0.171.0 和 Kimi 2.6.3 原生删除。
