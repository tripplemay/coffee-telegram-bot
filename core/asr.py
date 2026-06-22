"""语音转写：音频字节 → ffmpeg 归一化(16k 单声道 WAV) → 云 ASR → 文本。

网关无 ASR 模态，故外接云厂商。provider 由 ASR_PROVIDER 选择；统一入口 `transcribe()`。
渠道边缘（Telegram voice / 微信 SILK）只负责把原始音频字节递进来，转码+识别都在这里。

依赖：运行环境需有 **ffmpeg**（VPS: `apt-get install -y ffmpeg`）。具体 provider 客户端见 _PROVIDERS。
"""
from __future__ import annotations

import asyncio
import base64
import logging

import httpx

from core.config import get_settings

log = logging.getLogger("asr")


class ASRError(Exception):
    pass


def asr_enabled() -> bool:
    """是否已配置且可用的 ASR。未开启时渠道层走"请打字"兜底。"""
    s = get_settings()
    return bool(s.asr_provider) and s.asr_provider in _IMPLEMENTED


async def _to_wav16k(audio: bytes) -> bytes:
    """ffmpeg 把任意可识别容器（ogg/opus/mp3/m4a…）转成 16k 单声道 16-bit WAV。

    输入格式交给 ffmpeg 自动探测（Telegram 语音是 ogg/opus，能识别）。
    微信 SILK 不被 ffmpeg 原生识别，需先 SILK→PCM 解码（另行处理）。
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-i", "pipe:0", "-ar", "16000", "-ac", "1", "-f", "wav", "pipe:1",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as e:
        raise ASRError("ffmpeg 未安装（VPS: apt-get install -y ffmpeg）") from e
    out, err = await proc.communicate(audio)
    if proc.returncode != 0 or not out:
        raise ASRError(f"ffmpeg 转码失败: {err.decode(errors='ignore')[:200]}")
    return out


async def transcribe(audio: bytes) -> str:
    """原始音频字节 → 识别文本。未配置/失败抛 ASRError。"""
    s = get_settings()
    provider = s.asr_provider
    if provider not in _IMPLEMENTED:
        raise ASRError(f"ASR 未启用或 provider 未实现: {provider!r}")
    wav = await _to_wav16k(audio)
    fn = _PROVIDERS[provider]
    text = await fn(wav)
    return (text or "").strip()


# ---- 阿里 DashScope qwen3-asr-flash（OpenAI 兼容；单 Bearer key）----
_DASHSCOPE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
# 用 system 上下文给 ASR 做术语偏置，减少瑞幸专有名词被听错（在 ASR 层就纠正成瑞幸术语）
_ASR_CONTEXT = ("瑞幸咖啡点单语音。可能出现的词：生椰拿铁、丝绒拿铁、陨石拿铁、标准美式、冰萃美式、"
                "拿铁、卡布奇诺、瑞纳冰、轻轻茉莉、橙C美式、大杯、标准杯、热、冰、去冰、少冰、常温、"
                "无糖、少糖、半糖、三分糖、加浓缩、燕麦奶、点单、来一杯、要两杯")


async def _dashscope(wav: bytes) -> str:
    s = get_settings()
    b64 = base64.b64encode(wav).decode()
    body = {
        "model": "qwen3-asr-flash",
        "messages": [
            {"role": "system", "content": [{"text": _ASR_CONTEXT}]},
            {"role": "user", "content": [
                {"type": "input_audio", "input_audio": {"data": f"data:audio/wav;base64,{b64}"}}
            ]},
        ],
        "stream": False,
        "asr_options": {"enable_itn": True},  # 顺/逆文本归一化（口语数字→阿拉伯数字等）
    }
    headers = {"Authorization": f"Bearer {s.asr_api_key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(_DASHSCOPE_URL, headers=headers, json=body)
    if r.status_code != 200:
        raise ASRError(f"DashScope ASR HTTP {r.status_code}: {r.text[:200]}")
    try:
        content = r.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise ASRError(f"DashScope ASR 返回异常: {str(r.text)[:200]}") from e
    if isinstance(content, list):  # 多模态返回兜底：拼接 text 片段
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return content or ""


# provider 客户端：取 16k 单声道 WAV 字节 → 文本。
_PROVIDERS = {"dashscope": _dashscope}
_IMPLEMENTED: set[str] = set(_PROVIDERS)
