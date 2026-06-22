"""core/push.py 回推路由测试（不触网）。"""
from __future__ import annotations

import asyncio

from core import push


def test_push_guards_no_network():
    # 缺渠道/目标、未知渠道、未配置 wx 推送端点 → 一律 False，且不发起网络
    assert asyncio.run(push.push_to_channel(None, None, "x")) is False
    assert asyncio.run(push.push_to_channel("tg", None, "x")) is False
    assert asyncio.run(push.push_to_channel("xx", "t", "x")) is False
    assert asyncio.run(push.push_to_channel("wx", "u", "x")) is False  # WECHAT_PUSH_URL 未配置


def test_push_routes_to_correct_channel(monkeypatch):
    calls = []

    async def fake_tg(target, text):
        calls.append(("tg", target, text))
        return True

    async def fake_wx(target, text):
        calls.append(("wx", target, text))
        return True

    monkeypatch.setattr(push, "_push_telegram", fake_tg)
    monkeypatch.setattr(push, "_push_wechat", fake_wx)
    assert asyncio.run(push.push_to_channel("tg", "123", "hi")) is True
    assert asyncio.run(push.push_to_channel("wx", "u-abc", "yo")) is True
    assert calls == [("tg", "123", "hi"), ("wx", "u-abc", "yo")]
