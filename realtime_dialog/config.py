import uuid

import pyaudio

# Optional for search/tool-style APIs. The current realtime websocket flow
# does not use this value directly.
DOUBAO_API_KEY = "d5d5f2fb-fefb-4a8c-876b-7350385f4c8e"

# WebSocket connection settings for VolcEngine realtime dialog service.
# Important: X-Api-App-Key here is the app key expected by this websocket app,
# not the Secret Key shown elsewhere in the console.
ws_connect_config = {
    "base_url": "wss://openspeech.bytedance.com/api/v3/realtime/dialogue",
    "headers": {
        "X-Api-App-ID": "5685852259",
        "X-Api-Access-Key": "tCNf26V6T1NYLoauifmAB46QPKv1rs_0",
        "X-Api-Resource-Id": "volc.speech.dialog",
        "X-Api-App-Key": "PlgvMymc7f3tQnJ6",
        "X-Api-Connect-Id": str(uuid.uuid4()),
    },
}

VOICE_OPTIONS = [
    {
        "id": "zh_female_vv_jupiter_bigtts",
        "name": "vv 女声",
        "series": "o",
    },
    {
        "id": "zh_female_xiaohe_jupiter_bigtts",
        "name": "xiaohe 女声",
        "series": "o",
    },
    {
        "id": "zh_male_yunzhou_jupiter_bigtts",
        "name": "yunzhou 男声",
        "series": "o",
    },
    {
        "id": "zh_male_xiaotian_jupiter_bigtts",
        "name": "xiaotian 男声",
        "series": "o",
    },
]

DEFAULT_SPEAKER = "zh_female_vv_jupiter_bigtts"
DEFAULT_SPEED_RATIO = 3.0
DEFAULT_SYSTEM_ROLE = "你是豆包，请使用自然、简洁、友好的中文与用户进行实时语音通话。"

start_session_req = {
    "asr": {
        "extra": {
            "end_smooth_window_ms": 1500,
        },
    },
    "tts": {
        "speaker": DEFAULT_SPEAKER,
        "audio_config": {
            "channel": 1,
            "format": "pcm_s16le",
            "sample_rate": 24000,
        },
        "audio_params": {
            "speed_ratio": DEFAULT_SPEED_RATIO,
        },
    },
    "dialog": {
        "bot_name": "豆包",
        "system_role": DEFAULT_SYSTEM_ROLE,
        "speaking_style": "使用自然、清晰、亲和的中文语音风格。",
        "location": {
            "city": "北京",
        },
        "extra": {
            "strict_audit": False,
            "audit_response": "该内容当前无法回答，请换个问题试试。",
            "recv_timeout": 10,
            "input_mod": "audio",
        },
    },
}


def get_speed_range_for_voice(voice_id: str) -> tuple[float, float, float]:
    # O series voices support speed_ratio in [0.2, 3.0], step 0.1.
    if any(item["id"] == voice_id and item["series"] == "o" for item in VOICE_OPTIONS):
        return (0.2, 3.0, 0.1)
    return (0.2, 3.0, 0.1)


def clamp_speed_for_voice(voice_id: str, speed_ratio: float) -> float:
    min_v, max_v, step = get_speed_range_for_voice(voice_id)
    value = min(max(speed_ratio, min_v), max_v)
    steps = round((value - min_v) / step)
    snapped = min_v + steps * step
    return round(snapped, 1)


def speed_ratio_to_speech_rate(speed_ratio: float) -> int:
    # Map UI ratio [0.2, 3.0] to documented speech_rate range [-50, 100].
    min_ratio, max_ratio, _ = get_speed_range_for_voice(DEFAULT_SPEAKER)
    r = min(max(speed_ratio, min_ratio), max_ratio)
    scaled = -50 + (r - min_ratio) * 150.0 / (max_ratio - min_ratio)
    return int(round(min(max(scaled, -50), 100)))


def speech_rate_to_speed_ratio(speech_rate: int) -> float:
    sr = int(min(max(speech_rate, -50), 100))
    min_ratio, max_ratio, _ = get_speed_range_for_voice(DEFAULT_SPEAKER)
    ratio = min_ratio + (sr + 50) * (max_ratio - min_ratio) / 150.0
    return round(ratio, 1)


input_audio_config = {
    "chunk": 3200,
    "format": "pcm",
    "channels": 1,
    "sample_rate": 16000,
    "bit_size": pyaudio.paInt16,
}

output_audio_config = {
    "chunk": 3200,
    "format": "pcm_s16le",
    "channels": 1,
    "sample_rate": 24000,
    "bit_size": pyaudio.paInt16,
}

audio_upload_config = {
    "allowed_extensions": [".wav", ".mp3", ".m4a", ".flac", ".aac"],
    "target_channels": 1,
    "target_sample_rate": 16000,
    "target_sample_width": 2,
}
