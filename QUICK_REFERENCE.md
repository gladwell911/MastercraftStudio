# 快速参考 - 域名直接连接配置

## 🚀 快速开始（3步）

### 1️⃣ 设置环境变量

**Windows PowerShell:**
```powershell
$env:REMOTE_CONTROL_TOKEN="your_secret_token"
$env:REMOTE_CONTROL_DOMAIN="rc.tingyou.cc"
```

**Windows CMD:**
```cmd
set REMOTE_CONTROL_TOKEN=your_secret_token
set REMOTE_CONTROL_DOMAIN=rc.tingyou.cc
```

**Linux/Mac:**
```bash
export REMOTE_CONTROL_TOKEN="your_secret_token"
export REMOTE_CONTROL_DOMAIN="rc.tingyou.cc"
```

### 2️⃣ 启动程序
```bash
python main.py
```

### 3️⃣ 获取远程控制地址
- 在程序菜单中点击"复制远程控制地址"
- 在手机端程序中粘贴该地址

## 📋 环境变量说明

| 变量名 | 说明 | 示例 |
|-------|------|------|
| `REMOTE_CONTROL_TOKEN` | 安全令牌（必需） | `your_secret_token` |
| `REMOTE_CONTROL_DOMAIN` | 域名（必需） | `rc.tingyou.cc` |
| `REMOTE_CONTROL_HOST` | 本地监听地址（可选） | `0.0.0.0` |
| `REMOTE_CONTROL_PORT` | 本地监听端口（可选） | `18080` |

## 🔧 清理旧文件

```powershell
# PowerShell
.\cleanup_cloudflared.ps1

# 或 CMD
cleanup_cloudflared.bat
```

## ✅ 验证配置

```powershell
# 检查环境变量是否设置
echo $env:REMOTE_CONTROL_TOKEN
echo $env:REMOTE_CONTROL_DOMAIN

# 检查域名是否可访问
ping tingyou.cc

# 检查端口是否开放
Test-NetConnection -ComputerName tingyou.cc -Port 18080
```

## 🆘 常见问题

| 问题 | 解决方案 |
|------|--------|
| 连接失败 | 检查环境变量、防火墙、域名可访问性 |
| 令牌错误 | 确保 `REMOTE_CONTROL_TOKEN` 不为空 |
| 端口被占用 | 设置 `REMOTE_CONTROL_PORT` 为其他端口 |
| 无法访问 | 检查防火墙是否阻止 18080 端口 |

## 📚 详细文档

- `DOMAIN_CONNECTION_SETUP.md` - 完整配置指南
- `MIGRATION_SUMMARY.md` - 迁移总结
- `main.py` - 主程序源代码

## 🔐 安全建议

- 使用强随机令牌（32+ 字符）
- 定期更换令牌
- 在公网使用 WSS（加密）连接
- 限制防火墙规则

---

**提示**：首次使用前，请确保已设置环境变量并重启程序。
