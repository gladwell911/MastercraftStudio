"""Desktop defaults, limits, and model choices."""

import wx
from codex_client import DEFAULT_CODEX_MODEL


REQUESTING_TEXT = "正在请求..."
LEGACY_EMPTY_CURRENT_CHAT_TITLE = "心聊天"
EMPTY_CURRENT_CHAT_TITLE = "新聊天"
APP_STATE_FILE = "app_state.json"
APP_WINDOW_TITLE = "神匠工坊"
MAX_RECOVERY_ATTEMPTS = 3
CODEX_BACKGROUND_FLUSH_DELAY_MS = 250
CODEX_UI_EVENT_BATCH_SIZE = 25
CODEX_UI_INTERACTIVE_EVENT_BATCH_SIZE = 5
CODEX_UI_EVENT_BATCH_DELAY_MS = 10
IDLE_UI_REFRESH_DELAY_MS = 1000
UI_PERF_SLOW_THRESHOLD_MS = 50.0
UI_PERF_MAX_SLOW_SAMPLES = 100
ANSWER_LIST_DEFAULT_VISIBLE_ROWS = 100
ANSWER_LIST_EXPAND_ROWS = 100
EXECUTION_LIST_DEFAULT_VISIBLE_ROWS = 100
EXECUTION_LIST_EXPAND_ROWS = 100
REMOTE_STATE_DEFAULT_TURN_LIMIT = 10
NOTES_ENTRY_LABEL_MAX_CHARS = 160
CONTEXT_USAGE_SYNC_TURN_LIMIT = 120


DEFAULT_REMOTE_CONTROL_TOKEN = "h9k2m7p4q8x1z6v3t5n9c2r7d4s8j1f6"
DEFAULT_REMOTE_CONTROL_DOMAIN = "wss://rc.tingyou.cc/nats"
DEFAULT_REMOTE_NATS_PORT = 4222
REMOTE_NATS_PORT_FALLBACKS = (
    4622,
    4822,
    4223,
    4224,
    4522,
)
DEFAULT_REMOTE_NATS_WEBSOCKET_PORT = 18080
REMOTE_NATS_WEBSOCKET_PORT_FALLBACKS = (
    18081,
    18082,
    10080,
    28080,
    28081,
)
DEFAULT_REMOTE_CLOUDFLARED_ORIGIN_PORT = 18080
REMOTE_CLOUDFLARED_ORIGIN_PORT_FALLBACKS = (
    19080,
    19081,
    18082,
    10080,
    28080,
)
DEFAULT_REMOTE_NATS_CLOUDFLARED_URL = "wss://rc.tingyou.cc/nats"
REMOTE_CONTROL_HEALTH_TIMEOUT_SECONDS = 5


# aiohttp allows heartbeat / 2 for a PONG. The local startup probe budget
# must not impose a 2.5-second deadline on mobile connections.
REMOTE_CONTROL_WEBSOCKET_HEARTBEAT_SECONDS = 30


NAVIGATION_QUIET_SECONDS = 3.0
NAVIGATION_QUIET_ALT_KEYS = {ord(ch) for ch in "ASDFGBCasdfgbc"}
NAVIGATION_QUIET_KEYS = {
    wx.WXK_TAB,
    wx.WXK_UP,
    wx.WXK_DOWN,
    wx.WXK_LEFT,
    wx.WXK_RIGHT,
    wx.WXK_HOME,
    wx.WXK_END,
    wx.WXK_PAGEUP,
    wx.WXK_PAGEDOWN,
    wx.WXK_RETURN,
    wx.WXK_NUMPAD_ENTER,
    wx.WXK_SPACE,
}


MODEL_IDS = [
    "codex/main",
    "codex/gpt-5.4-medium",
    "codex/gpt-5.3-codex-spark-high",
    "claudecode/default",
    "kimi/main",
    "agy/main",
    "openclaw/main",
    "stepfun/step-3.5-flash",
    "meta-llama/llama-3.1-8b-instruct",
    "google/gemini-3.1-pro-preview",
    "google/gemini-3-pro-image-preview",
    "anthropic/claude-sonnet-4.6",
    "anthropic/claude-opus-4.6",
    "anthropic/claude-opus-4.5",
    "z-ai/glm-4.5-air",
    "qwen/qwen3.5-plus-02-15",
    "qwen/qwen3-8b",
    "minimax/minimax-m2.5",
    "minimax/minimax-m2-her",
    "z-ai/glm-5",
    "moonshotai/kimi-k2.5",
    "openai/gpt-5.2",
    "openai/gpt-5.2-codex",
    "openai/gpt-5.2-chat",
    "openai/gpt-5.2-pro",
    "bytedance-seed/seed-1.6",
    "doubao-2.0-pro",
    "doubao-2.0-lite",
    "doubao-2.0-mini",
    "xiaomi/mimo-v2-flash",
    "deepseek/deepseek-r1-0528",
    "deepseek/deepseek-r1-0528-qwen3-8b",
]
HIDDEN_MODEL_IDS = {
    "stepfun/step-3.5-flash",
    "meta-llama/llama-3.1-8b-instruct",
    "z-ai/glm-4.5-air",
    "qwen/qwen3-8b",
    "openai/gpt-5.2-codex",
    "openai/gpt-5.2-pro",
    "deepseek/deepseek-r1-0528-qwen3-8b",
}
VISIBLE_MODEL_IDS = [m for m in MODEL_IDS if m not in HIDDEN_MODEL_IDS]
DEFAULT_MODEL_ID = DEFAULT_CODEX_MODEL
STARTUP_DEFAULT_MODEL_ID = DEFAULT_MODEL_ID
APP_DIR_NAME = "神匠工坊"
VOICE_OPTIMIZE_MODEL = "openai/gpt-5.2-chat"
VOICE_OPTIMIZE_PROMPT = (
    "请对以下文本进行整理与优化，保持原意不变。要求：\n"
    "- 仅对所给文字进行优化整理，不对文字中的问题进行回答\n"
    "- 语言简洁、逻辑清晰、表达准确\n"
    "- 删除语气词、口头禅、赘述与重复信息\n"
    "- 不使用 Markdown 格式\n"
    "- 不添加额外标点符号或表情符号\n"
    "- 仅保留纯文字内容与自然段落结构\n"
    "- 不要空行\n"
    "- 不新增事实、不引入推测"
)
