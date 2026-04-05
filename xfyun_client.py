import base64
import hashlib
import hmac
import json
import os
import ssl
import threading
import time
from datetime import datetime, timezone
from email.utils import format_datetime
from urllib.parse import urlencode

import websocket

XF_DEFAULT_APPID = "44a75a75"
XF_DEFAULT_API_SECRET = "YjE2NWNkZmNlMjQ2ZDJmZTU4NWQ1MTYy"
XF_DEFAULT_API_KEY = "1fe30d61ded341c81a98a2a1befe0336"


class XFYunIATClient:
    def __init__(self, appid: str | None = None, api_key: str | None = None, api_secret: str | None = None) -> None:
        self.appid = appid or os.getenv("XF_APPID") or XF_DEFAULT_APPID
        self.api_key = api_key or os.getenv("XF_API_KEY") or XF_DEFAULT_API_KEY
        self.api_secret = api_secret or os.getenv("XF_API_SECRET") or XF_DEFAULT_API_SECRET
        self.host = "iat-api.xfyun.cn"
        self.path = "/v2/iat"
        self.base_url = f"wss://{self.host}{self.path}"

    def _build_url(self) -> str:
        now = datetime.now(timezone.utc)
        date = format_datetime(now, usegmt=True)
        signature_origin = f"host: {self.host}\ndate: {date}\nGET {self.path} HTTP/1.1"
        signature_sha = hmac.new(self.api_secret.encode("utf-8"), signature_origin.encode("utf-8"), digestmod=hashlib.sha256).digest()
        signature = base64.b64encode(signature_sha).decode("utf-8")
        authorization_origin = (
            f'api_key="{self.api_key}", algorithm="hmac-sha256", '
            f'headers="host date request-line", signature="{signature}"'
        )
        authorization = base64.b64encode(authorization_origin.encode("utf-8")).decode("utf-8")
        query = urlencode({"authorization": authorization, "date": date, "host": self.host})
        return f"{self.base_url}?{query}"

    def transcribe_pcm(self, pcm_bytes: bytes, sample_rate: int = 16000) -> str:
        return self.transcribe_pcm_segment(pcm_bytes, sample_rate=sample_rate)

    def transcribe_pcm_segment(
        self,
        pcm_bytes: bytes,
        sample_rate: int = 16000,
        retries: int = 1,
        on_delta=None,
    ) -> str:
        if not pcm_bytes:
            return ""

        url = self._build_url()
        done = threading.Event()
        error_box: list[str] = []
        text_parts: list[str] = []
        emitted_len = 0

        def _append_text(payload: dict) -> None:
            nonlocal emitted_len
            try:
                ws_list = payload.get("data", {}).get("result", {}).get("ws", [])
                for ws_item in ws_list:
                    for cw in ws_item.get("cw", []):
                        text = str(cw.get("w") or "")
                        if text:
                            text_parts.append(text)
                if on_delta:
                    full_text = "".join(text_parts)
                    if len(full_text) > emitted_len:
                        delta = full_text[emitted_len:]
                        emitted_len = len(full_text)
                        if delta:
                            on_delta(delta)
            except Exception:
                return

        def on_open(ws):
            frame_size = 3200  # 16k * 16bit * 100ms
            interval = 0.01
            idx = 0
            status = 0
            while idx < len(pcm_bytes):
                chunk = pcm_bytes[idx: idx + frame_size]
                idx += frame_size
                if idx >= len(pcm_bytes):
                    status = 2
                data = {
                    "common": {"app_id": self.appid},
                    "business": {
                        "domain": "iat",
                        "language": "zh_cn",
                        "accent": "mandarin",
                        "vinfo": 1,
                        "vad_eos": 4000,
                    },
                    "data": {
                        "status": status if status != 0 else 0,
                        "format": f"audio/L16;rate={sample_rate}",
                        "encoding": "raw",
                        "audio": base64.b64encode(chunk).decode("utf-8"),
                    },
                }
                if status != 0:
                    data.pop("common", None)
                    data.pop("business", None)
                ws.send(json.dumps(data))
                time.sleep(interval)

        def on_message(ws, message):
            try:
                obj = json.loads(message)
            except json.JSONDecodeError:
                return
            code = int(obj.get("code", -1))
            if code != 0:
                error_box.append(str(obj.get("message") or "讯飞返回错误"))
                done.set()
                ws.close()
                return
            _append_text(obj)
            if int(obj.get("data", {}).get("status", 0)) == 2:
                done.set()
                ws.close()

        def on_error(_ws, error):
            error_box.append(str(error))
            done.set()

        def on_close(_ws, _code, _msg):
            done.set()

        ws_app = websocket.WebSocketApp(
            url,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )
        t = threading.Thread(target=ws_app.run_forever, kwargs={"sslopt": {"cert_reqs": ssl.CERT_NONE}}, daemon=True)
        t.start()
        done.wait(timeout=30)

        if error_box:
            if retries > 0:
                return self.transcribe_pcm_segment(
                    pcm_bytes,
                    sample_rate=sample_rate,
                    retries=retries - 1,
                    on_delta=on_delta,
                )
            raise RuntimeError(f"讯飞识别失败：{error_box[-1]}")
        return "".join(text_parts).strip()

    def transcribe_long_pcm(self, pcm_bytes: bytes, sample_rate: int = 16000, segment_seconds: int = 45, on_delta=None) -> str:
        if not pcm_bytes:
            return ""
        bytes_per_second = sample_rate * 2  # 16bit mono
        seg_size = max(bytes_per_second * max(segment_seconds, 1), bytes_per_second)
        all_parts: list[str] = []

        idx = 0
        while idx < len(pcm_bytes):
            seg = pcm_bytes[idx: idx + seg_size]
            idx += seg_size
            try:
                part = self.transcribe_pcm_segment(
                    seg,
                    sample_rate=sample_rate,
                    retries=1,
                    on_delta=on_delta,
                )
            except Exception:
                part = ""
            if part:
                all_parts.append(part)

        return "".join(all_parts).strip()
