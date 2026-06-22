"""高德地理编码 + agent geocodeAddress 本地路由测试（mock 不触网）。"""
from __future__ import annotations

import asyncio

from core import amap


def _run(coro):
    return asyncio.run(coro)


class _Resp:
    def __init__(self, data):
        self._d = data
        self.status_code = 200

    def json(self):
        return self._d


class _Client:
    def __init__(self, data):
        self._d = data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, params=None):
        return _Resp(self._d)


def _settings(key):
    return type("S", (), {"amap_key": key})()


def test_geocode_address_parses(monkeypatch):
    data = {"status": "1", "geocodes": [
        {"location": "104.082003,30.461586", "formatted_address": "四川省成都市天府新区港汇紫光星云中心"}]}
    monkeypatch.setattr(amap, "get_settings", lambda: _settings("k"))
    monkeypatch.setattr(amap.httpx, "AsyncClient", lambda *a, **k: _Client(data))
    res = _run(amap.geocode_address("成都港汇紫光星云中心"))
    assert res is not None
    lng, lat, formatted = res
    assert abs(lng - 104.082003) < 1e-6 and abs(lat - 30.461586) < 1e-6 and "港汇" in formatted


def test_geocode_no_key(monkeypatch):
    monkeypatch.setattr(amap, "get_settings", lambda: _settings(""))
    assert _run(amap.geocode_address("成都xx")) is None


def test_geocode_no_result(monkeypatch):
    monkeypatch.setattr(amap, "get_settings", lambda: _settings("k"))
    monkeypatch.setattr(amap.httpx, "AsyncClient", lambda *a, **k: _Client({"status": "0", "geocodes": []}))
    assert _run(amap.geocode_address("不存在的地方")) is None


def test_regeo_parses_and_fallback(monkeypatch):
    monkeypatch.setattr(amap, "get_settings", lambda: _settings("k"))
    monkeypatch.setattr(amap.httpx, "AsyncClient",
                        lambda *a, **k: _Client({"status": "1", "regeocode": {"formatted_address": "四川省成都市天府新区甲"}}))
    assert "成都" in _run(amap.regeo(104.08, 30.46))
    monkeypatch.setattr(amap, "get_settings", lambda: _settings(""))
    assert _run(amap.regeo(1, 2)) == "我的位置"


def test_geocode_tool_in_schema():
    from bot.tools import NON_MCP_TOOLS, TOOL_NAMES
    assert "geocodeAddress" in TOOL_NAMES
    assert "geocodeAddress" in NON_MCP_TOOLS


def test_agent_routes_geocode_locally_not_mcp(monkeypatch):
    from bot.agent import OrderingAgent

    class FakeMCP:
        async def call_tool(self, *a, **k):
            raise AssertionError("geocodeAddress 不应走瑞幸 MCP")

    agent = OrderingAgent(FakeMCP())  # type: ignore[arg-type]

    async def fake_geo(addr):
        assert addr == "成都港汇紫光星云中心"
        return (104.082, 30.4616, "四川省成都市天府新区港汇紫光星云中心")

    monkeypatch.setattr("bot.agent.amap.geocode_address", fake_geo)
    res = _run(agent._dispatch("tok", "geocodeAddress", '{"address":"成都港汇紫光星云中心"}'))
    assert res["longitude"] == 104.082 and res["latitude"] == 30.4616 and "港汇" in res["formatted_address"]
