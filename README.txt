项目名称：神匠工坊

一、环境要求
1. Windows 10 / 11
2. Python 3.10+

二、安装依赖
在项目目录下执行：

```bash
python -m pip install -r requirements.txt
```

三、配置环境变量
如需使用 OpenRouter 接口，请先设置 API Key：

```bash
set OPENROUTER_API_KEY=你的Key
```

或：

```bash
setx OPENROUTER_API_KEY "你的Key"
```

四、运行程序

```bash
python main.py
```

五、基本操作
1. 输入框：
   - `Enter`：换行
   - `Ctrl+Enter`：发送
   - `Alt+S`：发送
   - `Alt+N`：新聊天
2. 回答列表支持键盘浏览，按回车可打开回答详情网页。
3. 发送后状态栏会显示“已发送”，完成后显示“答复完成”。
4. 使用 OpenClaw 时，程序会自动同步相关会话状态。

六、说明
1. 项目主窗口名称为“神匠工坊”。
2. 历史聊天、当前会话和恢复状态都会保存在本地数据目录中。
3. 如遇到异常，请先检查 `OPENROUTER_API_KEY` 是否已正确配置。