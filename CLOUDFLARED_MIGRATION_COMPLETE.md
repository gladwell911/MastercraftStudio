# Cloudflared 迁移完成

## 概述

已成功从 cloudflared 隧道方案迁移到域名直接连接方案（rc.tingyou.cc）。

## 删除的文件

### PowerShell 脚本
- `start_remote_quicktunnel.ps1`
- `stop_remote_quicktunnel.ps1`

### 配置和管理文件
- `cleanup_cloudflared.bat`
- `cleanup_cloudflared.ps1`
- `cloudflared_manager.py`
- `.cloudflared/` 目录

## 新增文件

- `REMOTE_CONTROL_SETUP.md` - 详细的配置指南

## 快速开始

### 1. 设置环境变量

```bash
# Windows PowerShell
$env:REMOTE_CONTROL_TOKEN="your_secret_token"
$env:REMOTE_CONTROL_DOMAIN="rc.tingyou.cc"
python main.py
```

### 2. 获取连接地址

程序启动后，WebSocket 连接地址为：
```
wss://rc.tingyou.cc/ws?token=your_secret_token
```

### 3. 在手机端连接

使用上述地址在手机端程序中进行连接。

## 配置说明

| 环境变量 | 说明 | 默认值 |
|---------|------|--------|
| REMOTE_CONTROL_TOKEN | 认证令牌（必需） | 无 |
| REMOTE_CONTROL_DOMAIN | 远程域名 | rc.tingyou.cc |
| REMOTE_CONTROL_HOST | 本地监听地址 | 0.0.0.0 |
| REMOTE_CONTROL_PORT | 本地监听端口 | 18080 |

## 验证

所有 cloudflared 相关的代码和文件已被完全移除。程序现在使用纯 WebSocket 实现远程控制，无需任何隧道工具。

## 安全建议

1. 使用强随机令牌（至少32个字符）
2. 定期更换令牌
3. 在公网环境中使用 HTTPS/WSS
4. 配置防火墙只允许必要的端口访问
