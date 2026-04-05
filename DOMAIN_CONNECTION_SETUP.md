# 域名直接连接配置指南

本项目已完全迁移到使用域名直接连接的方案，不再依赖 cloudflared。

## 快速开始

### 1. 设置环境变量

在启动程序前，设置以下环境变量：

```bash
# 远程控制令牌（必需）- 用于安全认证
REMOTE_CONTROL_TOKEN=your_secret_token

# 远程控制域名（必需）- 使用 rc.tingyou.cc
REMOTE_CONTROL_DOMAIN=rc.tingyou.cc
```

### 2. Windows 环境变量设置方法

#### 方法 A：系统环境变量（永久）
1. 按 `Win + X`，选择"系统"
2. 点击"高级系统设置"
3. 点击"环境变量"按钮
4. 在"用户变量"或"系统变量"中点击"新建"
5. 添加：
   - 变量名：`REMOTE_CONTROL_TOKEN`
   - 变量值：`your_secret_token`
6. 重复添加 `REMOTE_CONTROL_DOMAIN=rc.tingyou.cc`
7. 点击"确定"，重启程序

#### 方法 B：PowerShell（临时）
```powershell
$env:REMOTE_CONTROL_TOKEN="your_secret_token"
$env:REMOTE_CONTROL_DOMAIN="rc.tingyou.cc"
# 然后启动程序
python main.py
```

#### 方法 C：CMD（临时）
```cmd
set REMOTE_CONTROL_TOKEN=your_secret_token
set REMOTE_CONTROL_DOMAIN=rc.tingyou.cc
python main.py
```

### 3. Linux/Mac 环境变量设置

```bash
export REMOTE_CONTROL_TOKEN="your_secret_token"
export REMOTE_CONTROL_DOMAIN="rc.tingyou.cc"
python main.py
```

## 域名格式支持

`REMOTE_CONTROL_DOMAIN` 支持以下格式，程序会自动转换：

| 格式 | 转换结果 |
|------|--------|
| `rc.tingyou.cc` | `wss://rc.tingyou.cc/ws?token=...` |
| `https://rc.tingyou.cc` | `wss://rc.tingyou.cc/ws?token=...` |
| `http://rc.tingyou.cc` | `ws://rc.tingyou.cc/ws?token=...` |
| `wss://rc.tingyou.cc` | `wss://rc.tingyou.cc/ws?token=...` |
| `ws://rc.tingyou.cc` | `ws://rc.tingyou.cc/ws?token=...` |

## 获取远程控制地址

程序启动后，可以通过以下方式获取完整的 WebSocket 连接地址：

1. 在程序菜单中找到"复制远程控制地址"选项
2. 点击后会复制完整的 WebSocket URL 到剪贴板
3. 在手机端程序中粘贴该地址进行连接

## 清理旧的 cloudflared 文件

如果需要完全清理旧的 cloudflared 相关文件：

```bash
# 删除 cloudflared 可执行文件
rm cloudflared.exe

# 删除 cloudflared 配置目录（可选，如果不再需要）
rm -rf .cloudflared/
```

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

## 迁移说明

从 cloudflared 方案迁移到域名直接连接：

### 已删除的文件/功能
- `cloudflared_manager.py` - 已删除
- cloudflared 自动启动逻辑 - 已移除
- `.cloudflared` 目录中的自动化脚本 - 已移除

### 新方案优势
- ✅ 无需额外的 cloudflared 进程
- ✅ 直接使用域名连接，更简洁
- ✅ 配置更灵活，支持多种域名格式
- ✅ 更低的资源占用
- ✅ 更快的连接速度

## 相关文件

- `main.py` - 主程序，包含 WebSocket 服务器实现
- `remote_ws.py` - WebSocket 服务器实现
- `remote_protocol.py` - 远程控制协议定义
