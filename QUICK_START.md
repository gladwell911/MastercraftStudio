# 快速参考指南

## 域名连接配置 - tingyou.cc:18080

### 环境变量设置

```bash
# 必需
REMOTE_CONTROL_TOKEN=your_secret_token

# 可选（默认为 tingyou.cc:18080）
REMOTE_CONTROL_DOMAIN=tingyou.cc:18080
```

### Windows 快速设置

**PowerShell:**
```powershell
$env:REMOTE_CONTROL_TOKEN="your_secret_token"
$env:REMOTE_CONTROL_DOMAIN="tingyou.cc:18080"
python main.py
```

**CMD:**
```cmd
set REMOTE_CONTROL_TOKEN=your_secret_token
set REMOTE_CONTROL_DOMAIN=tingyou.cc:18080
python main.py
```

### Linux/Mac 快速设置

```bash
export REMOTE_CONTROL_TOKEN="your_secret_token"
export REMOTE_CONTROL_DOMAIN="tingyou.cc:18080"
python main.py
```

### 获取 WebSocket URL

程序启动后，在菜单中选择"复制远程控制地址"，会自动生成：
```
wss://tingyou.cc:18080/ws?token=your_secret_token
```

### 测试配置

```bash
python test_domain_connection.py
```

## 支持的域名格式

| 输入格式 | 转换结果 |
|---------|--------|
| `tingyou.cc:18080` | `wss://tingyou.cc:18080/ws?token=...` |
| `https://tingyou.cc:18080` | `wss://tingyou.cc:18080/ws?token=...` |
| `http://tingyou.cc:18080` | `ws://tingyou.cc:18080/ws?token=...` |
| `wss://tingyou.cc:18080` | `wss://tingyou.cc:18080/ws?token=...` |
| `ws://tingyou.cc:18080` | `ws://tingyou.cc:18080/ws?token=...` |

## 故障排除

| 问题 | 解决方案 |
|------|--------|
| 连接失败 | 检查 REMOTE_CONTROL_TOKEN 是否设置 |
| 域名无法访问 | 检查防火墙是否允许 18080 端口 |
| 令牌不匹配 | 确保手机端和电脑端使用相同的令牌 |
| 程序无法启动 | 检查 18080 端口是否被占用 |

## 相关文件

- `REMOTE_CONTROL_SETUP.md` - 详细配置指南
- `test_domain_connection.py` - 配置测试脚本
- `remote_ws.py` - WebSocket 服务器实现
- `claudecode_remote_client.py` - Claude Code 远程客户端
