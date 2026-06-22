"""core/asr.py 测试：开关逻辑、DashScope 请求/解析（mock 不触网）、ffmpeg 转码（有则测）。"""
from __future__ import annotations

import asyncio
import shutil
import subprocess

import pytest

from core import asr


def test_asr_enabled(monkeypatch):
    class S:
        asr_provider = ""
        asr_api_key = ""

    monkeypatch.setattr(asr, "get_settings", lambda: S())
    assert asr.asr_enabled() is False

    class S2:
        asr_provider = "dashscope"
        asr_api_key = "k"

    monkeypatch.setattr(asr, "get_settings", lambda: S2())
    assert asr.asr_enabled() is True

    class S3:
        asr_provider = "unknown"
        asr_api_key = "k"

    monkeypatch.setattr(asr, "get_settings", lambda: S3())
    assert asr.asr_enabled() is False


def test_dashscope_parses_content(monkeypatch):
    class S:
        asr_api_key = "k"

    monkeypatch.setattr(asr, "get_settings", lambda: S())

    class FakeResp:
        status_code = 200
        text = ""

        def json(self):
            return {"choices": [{"message": {"content": "来杯热的生椰拿铁"}}]}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            # 校验请求形态：base64 data URL + 正确 model
            assert json["model"] == "qwen3-asr-flash"
            assert json["messages"][-1]["content"][0]["input_audio"]["data"].startswith("data:audio/wav;base64,")
            assert headers["Authorization"] == "Bearer k"
            return FakeResp()

    monkeypatch.setattr(asr.httpx, "AsyncClient", FakeClient)
    out = asyncio.run(asr._dashscope(b"fake-wav-bytes"))
    assert out == "来杯热的生椰拿铁"


def test_dashscope_raises_on_http_error(monkeypatch):
    class S:
        asr_api_key = "k"

    monkeypatch.setattr(asr, "get_settings", lambda: S())

    class FakeResp:
        status_code = 401
        text = "unauthorized"

        def json(self):
            return {}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            return FakeResp()

    monkeypatch.setattr(asr.httpx, "AsyncClient", FakeClient)
    with pytest.raises(asr.ASRError):
        asyncio.run(asr._dashscope(b"x"))


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg 未安装")
def test_to_wav16k_produces_wav():
    raw = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=0.2", "-f", "wav", "pipe:1"],
        capture_output=True).stdout
    out = asyncio.run(asr._to_wav16k(raw))
    assert out[:4] == b"RIFF" and len(out) > 44  # 合法 WAV（头 44B + 数据）
