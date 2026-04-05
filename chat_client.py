import json
import re
from typing import Callable

import requests

BASE_URL = "https://openrouter.ai/api/v1"
CHAT_COMPLETIONS_PATH = "/chat/completions"
DEFAULT_MODEL = "openai/gpt-5.2"
TIMEOUT_SECONDS = 60


class ChatClient:
    def __init__(self, api_key: str, base_url: str = BASE_URL, model: str = DEFAULT_MODEL, timeout: int = TIMEOUT_SECONDS) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost",
            "X-Title": "Accessible ChatGPT Desktop",
        }

    def _build_messages(self, user_text: str, history_turns: list[dict] | None = None) -> list[dict]:
        messages = [
            {
                "role": "system",
                "content": "请使用 Markdown 格式回答，尽量使用标题、段落、列表等结构化格式。不要使用任何表情符号（emoji）。",
            },
        ]
        for turn in history_turns or []:
            q = str(turn.get("question") or "").strip()
            a = str(turn.get("answer_md") or "").strip()
            if q:
                messages.append({"role": "user", "content": q})
            if a:
                messages.append({"role": "assistant", "content": a})
        messages.append({"role": "user", "content": user_text})
        return messages

    def stream_chat(self, user_text: str, on_delta: Callable[[str], None], history_turns: list[dict] | None = None) -> str:
        messages = self._build_messages(user_text, history_turns=history_turns)
        need_web = self._should_use_web(user_text)
        if not need_web:
            return self._stream_request(self.model, messages, on_delta)

        # 本轮按需开启联网。优先 online 变体，失败则回退 plugins:web。
        try:
            return self._stream_request(f"{self.model}:online", messages, on_delta)
        except RuntimeError as online_err:
            try:
                return self._stream_request(self.model, messages, on_delta, plugins=[{"id": "web"}])
            except RuntimeError:
                raise online_err

    @staticmethod
    def is_no_endpoint_error(error_text: str, model: str | None = None) -> bool:
        txt = str(error_text or "")
        if "HTTP 404" not in txt:
            return False
        if "No endpoints found for" not in txt:
            return False
        if model:
            return re.search(re.escape(model), txt) is not None
        return True

    @staticmethod
    def _first_choice(data: dict) -> dict:
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            return {}
        first = choices[0]
        return first if isinstance(first, dict) else {}

    def _stream_request(self, model: str, messages: list[dict], on_delta: Callable[[str], None], plugins: list[dict] | None = None) -> str:
        url = f"{self.base_url}{CHAT_COMPLETIONS_PATH}"
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        if plugins:
            payload["plugins"] = plugins

        parts: list[str] = []
        try:
            with requests.post(url, headers=self._headers(), json=payload, timeout=self.timeout, stream=True) as resp:
                if resp.status_code == 401:
                    raise RuntimeError("请求失败：401 未授权。请检查 OPENROUTER_API_KEY。")
                if resp.status_code >= 400:
                    raise RuntimeError(f"请求失败：HTTP {resp.status_code}。{self._extract_error_detail(resp)}")

                for raw_line in resp.iter_lines(decode_unicode=False):
                    if not raw_line:
                        continue
                    line = raw_line.decode("utf-8", errors="replace") if isinstance(raw_line, bytes) else str(raw_line)
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    delta = self._first_choice(obj).get("delta", {}).get("content", "")
                    if delta:
                        parts.append(delta)
                        on_delta(delta)
        except requests.Timeout as exc:
            raise RuntimeError("请求超时：60 秒内未完成，请稍后重试。") from exc
        except requests.RequestException as exc:
            raise RuntimeError(f"网络请求失败：{exc}") from exc

        return "".join(parts)

    def _should_use_web(self, user_text: str) -> bool:
        url = f"{self.base_url}{CHAT_COMPLETIONS_PATH}"
        payload = {
            "model": self.model,
            "stream": False,
            "temperature": 0,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是联网判断器。仅输出 YES 或 NO。"
                        "当问题明显需要最新事实、新闻、实时数据、近期价格、政策/赛程/版本变更时输出 YES；"
                        "否则输出 NO。不要输出其他字符。"
                    ),
                },
                {"role": "user", "content": user_text},
            ],
        }
        try:
            resp = requests.post(url, headers=self._headers(), json=payload, timeout=min(self.timeout, 20))
            if resp.status_code >= 400:
                return False
            data = resp.json()
            text = str(self._first_choice(data).get("message", {}).get("content", "")).strip().upper()
            return text.startswith("YES")
        except Exception:
            return False

    def generate_chat_title(self, transcript: str) -> str:
        url = f"{self.base_url}{CHAT_COMPLETIONS_PATH}"
        payload = {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": "你是标题助手。请根据对话内容生成一个简洁中文标题，长度 8-20 字，不要引号。"},
                {"role": "user", "content": transcript},
            ],
            "temperature": 0.2,
        }
        try:
            resp = requests.post(url, headers=self._headers(), json=payload, timeout=self.timeout)
            if resp.status_code >= 400:
                return ""
            data = resp.json()
            text = str(self._first_choice(data).get("message", {}).get("content", "")).strip()
            if not text:
                return ""
            return text.splitlines()[0][:40]
        except Exception:
            return ""

    def rewrite_text(self, text: str, instruction: str, model: str | None = None) -> str:
        url = f"{self.base_url}{CHAT_COMPLETIONS_PATH}"
        payload = {
            "model": model or self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": instruction},
                {"role": "user", "content": text},
            ],
            "temperature": 0.2,
        }
        try:
            resp = requests.post(url, headers=self._headers(), json=payload, timeout=self.timeout)
            if resp.status_code >= 400:
                return ""
            data = resp.json()
            out = str(self._first_choice(data).get("message", {}).get("content", "")).strip()
            return out
        except Exception:
            return ""

    def _extract_error_detail(self, resp: requests.Response) -> str:
        try:
            data = resp.json()
            msg = data.get("error", {}).get("message")
            if msg:
                return f"错误信息：{msg}"
        except ValueError:
            pass
        txt = resp.text.strip()
        return f"响应内容：{txt[:300]}" if txt else "服务端未返回更多错误信息。"


