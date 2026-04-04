# 远程控制配置指南

本项目支持通过域名 `tingyou.cc:18080` 进行远程控制。

## 快速开始

### 1. 设置环境变量

在启动程序前，设置以下环境变量：

```bash
# 远程控制令牌（必需）- 用于安全认证
REMOTE_CONTROL_TOKEN=your_secret_token

# 远程控制域名（可选）- 默认为 tingyou.cc:18080
REMOTE_CONTROL_DOMAIN=tingyou.cc:18080
```

### 2. Windows 环境变量设置

#### 方法 A：系统环境变量（永久）
1. 按 `Win + X`，选择"系统"
2. 点击"高级系统设置"
3. 点击"环境变量"按钮
4. 在"用户变量"或"系统变量"中点击"新建"
5. 添加：
   - 变量名：`REMOTE_CONTROL_TOKEN`
   - 变量值：`your_secret_token`
6. 重复添加 `REMOTE_CONTROL_DOMAIN=tingyou.cc:18080`
7. 点击"确定"，重启程序

#### 方法 B：PowerShell（临时）
```powershell
$env:REMOTE_CONTROL_TOKEN="your_secret_token"
$env:REMOTE_CONTROL_DOMAIN="tingyou.cc:18080"
python main.py
```

#### 方法 C：CMD（临时）
```cmd
set REMOTE_CONTROL_TOKEN=your_secret_token
set REMOTE_CONTROL_DOMAIN=tingyou.cc:18080
python main.py
```

### 3. Linux/Mac 环境变量设置

```bash
export REMOTE_CONTROL_TOKEN="your_secret_token"
export REMOTE_CONTROL_DOMAIN="tingyou.cc:18080"
python main.py
```

## 域名格式支持

`REMOTE_CONTROL_DOMAIN` 支持以下格式，程序会自动转换：

| 格式 | 转换结果 |
|------|--------|
| `tingyou.cc:18080` | `wss://tingyou.cc:18080/ws?token=...` |
| `https://tingyou.cc:18080` | `wss://tingyou.cc:18080/ws?token=...` |
| `http://tingyou.cc:18080` | `ws://tingyou.cc:18080/ws?token=...` |
| `wss://tingyou.cc:18080` | `wss://tingyou.cc:18080/ws?token=...` |
| `ws://tingyou.cc:18080` | `ws://tingyou.cc:18080/ws?token=...` |

## 获取远程控制地址

程序启动后，可以通过以下方式获取完整的 WebSocket 连接地址：

1. 在程序菜单中找到"复制远程控制地址"选项
2. 点击后会复制完整的 WebSocket URL 到剪贴板
3. 在手机端程序中粘贴该地址进行连接

## 服务器配置

### 本地服务器

- 默认监听地址：`0.0.0.0:18080`
- 可通过环境变量 `REMOTE_CONTROL_HOST` 和 `REMOTE_CONTROL_PORT` 自定义

### 远程访问

- 使用域名 `tingyou.cc:18080` 从外部访问
- 确保防火墙允许 18080 端口的入站连接
- 所有连接都需要提供正确的 `REMOTE_CONTROL_TOKEN`

## 故障排除

### 连接失败

1. **检查环境变量是否正确设置**
   ```bash
   # Windows PowerShell
   echo $env:REMOTE_CONTROL_TOKEN
   echo $env:REMOTE_CONTROL_DOMAIN
   
   # Linux/Mac
   echo $REMOTE_CONTROL_TOKEN
   echo $REMOTE_CONTROL_DOMAIN
   ```

2. **检查域名是否可访问**
   ```bash
   ping tingyou.cc
   ```

3. **检查防火墙设置**
   - 确保 18080 端口未被防火墙阻止
   - Windows 防火墙可能需要添加例外规则

4. **查看程序状态栏**
   - 程序启动时会在状态栏显示 WebSocket 服务器状态
   - 如果显示错误信息，检查错误内容

### 令牌问题

- 确保 `REMOTE_CONTROL_TOKEN` 不为空
- 令牌应该是一个安全的随机字符串
- 手机端连接时使用的令牌必须与服务端一致

## 安全建议

1. **使用强令牌**
   - 建议使用至少 32 个字符的随机字符串
   - 可以使用在线工具生成：https://www.uuidgenerator.net/

2. **定期更换令牌**
   - 定期更新 `REMOTE_CONTROL_TOKEN` 值

3. **使用 HTTPS/WSS**
   - 在公网环境中，建议使用 `https://` 或 `wss://` 协议
   - 确保域名配置使用安全连接

4. **防火墙配置**
   - 只在需要时开放 18080 端口
   - 考虑使用 VPN 或其他安全隧道

## 相关文件

- `main.py` - 主程序，包含 WebSocket 服务器实现
- `remote_ws.py` - WebSocket 服务器实现
- `remote_protocol.py` - 远程控制协议定义
- `claudecode_remote_client.py` - Claude Code 远程客户端实现
