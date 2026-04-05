# Cloudflared 迁移总结

## 迁移完成

已成功从 cloudflared 方案迁移到域名直接连接方案。

## 删除的文件和目录

- `cloudflared.exe` - cloudflared 可执行文件
- `.cloudflared/` - cloudflared 配置目录
- `__pycache__/cloudflared_manager.cpython-*.pyc` - cloudflared 管理器缓存文件

## 新增功能

### 1. 远程 WebSocket 服务器

在 `main.py` 中添加了以下方法：

- `_start_remote_ws_server_if_configured()` - 启动远程 WebSocket 服务器
- `_read_remote_control_token()` - 读取远程控制令牌
- `_build_remote_ws_url()` - 构建远程 WebSocket URL
- `_on_copy_remote_ws_url()` - 复制远程控制地址到剪贴板
- `_remote_api_*_ui()` - 远程 API 处理方法

### 2. 自动启动

程序启动时会自动检查 `REMOTE_CONTROL_TOKEN` 环境变量，如果设置了该变量，会自动启动 WebSocket 服务器。

### 3. 优雅关闭

程序关闭时会自动停止远程 WebSocket 服务器。

## 配置方法

### 环境变量

```bash
# 远程控制令牌（必需）
REMOTE_CONTROL_TOKEN=your_secret_token

# 远程控制域名（可选，默认为 rc.tingyou.cc）
REMOTE_CONTROL_DOMAIN=rc.tingyou.cc

# 本地监听地址（可选，默认为 0.0.0.0）
REMOTE_CONTROL_HOST=0.0.0.0

# 本地监听端口（可选，默认从 REMOTE_CONTROL_DOMAIN 中提取或使用 18080）
REMOTE_CONTROL_PORT=18080
```

### 域名格式支持

`REMOTE_CONTROL_DOMAIN` 支持以下格式：

- `rc.tingyou.cc` - 自动转换为 `wss://rc.tingyou.cc/ws?token=...`
- `https://rc.tingyou.cc` - 转换为 `wss://rc.tingyou.cc/ws?token=...`
- `http://rc.tingyou.cc` - 转换为 `ws://rc.tingyou.cc/ws?token=...`
- `wss://rc.tingyou.cc` - 直接使用 `wss://rc.tingyou.cc/ws?token=...`
- `ws://rc.tingyou.cc` - 直接使用 `ws://rc.tingyou.cc/ws?token=...`

## 优势

1. **无需额外进程** - 不需要运行 cloudflared 进程
2. **更简洁** - 直接使用域名连接
3. **更灵活** - 支持多种域名格式
4. **更低的资源占用** - 减少系统资源消耗
5. **更容易维护** - 配置更简单

## 使用示例

### Windows PowerShell

```powershell
$env:REMOTE_CONTROL_TOKEN="your_secret_token"
$env:REMOTE_CONTROL_DOMAIN="rc.tingyou.cc"
# 然后运行程序
python main.py
```

### Windows CMD

```cmd
set REMOTE_CONTROL_TOKEN=your_secret_token
set REMOTE_CONTROL_DOMAIN=rc.tingyou.cc
python main.py
```

### Linux/Mac

```bash
export REMOTE_CONTROL_TOKEN="your_secret_token"
export REMOTE_CONTROL_DOMAIN="rc.tingyou.cc"
python main.py
```

## 验证

程序启动后，如果配置了 `REMOTE_CONTROL_TOKEN`，状态栏会显示：
```
远程 WebSocket 已启动：ws://0.0.0.0:18080/ws
```

手机端程序可以使用以下 URL 连接：
```
wss://rc.tingyou.cc/ws?token=your_secret_token
```

## 注意事项

1. 确保防火墙允许 18080 端口的入站连接
2. 确保域名 `tingyou.cc` 正确指向你的电脑
3. 令牌应该是一个安全的随机字符串
4. 所有连接都需要提供正确的令牌进行身份验证
