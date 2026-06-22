"""高德地理编码（集中复用）。

- geocode_address：地址/地名/地标 → GCJ-02 经纬度（agent 的 geocodeAddress 工具、微信 /loc 用）。
- regeo：GCJ-02 经纬度 → 可读地址（定位网页逆编码用）。
均需 AMAP_KEY；未配置/失败优雅返回 None / 默认标签。坐标系与瑞幸一致（GCJ-02）。
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

from core.config import get_settings

log = logging.getLogger("amap")


async def geocode_address(address: str) -> Optional[tuple[float, float, str]]:
    """地址/地名/地标 → (lng, lat, formatted_address)，GCJ-02。未配 key/无结果/失败返回 None。"""
    key = get_settings().amap_key
    if not key:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get("https://restapi.amap.com/v3/geocode/geo",
                            params={"address": address, "key": key})
            data = r.json()
        if data.get("status") == "1" and data.get("geocodes"):
            g = data["geocodes"][0]
            lng_s, lat_s = g["location"].split(",")
            return (float(lng_s), float(lat_s), g.get("formatted_address") or address)
    except Exception as e:
        log.warning("geocode failed for %r: %s", address, e)
    return None


async def regeo(lng: float, lat: float) -> str:
    """GCJ-02 坐标 → 可读地址；未配 key/失败回退『我的位置』。"""
    key = get_settings().amap_key
    if not key:
        return "我的位置"
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get("https://restapi.amap.com/v3/geocode/regeo",
                            params={"location": f"{lng},{lat}", "key": key, "radius": 200})
            data = r.json()
        if data.get("status") == "1":
            addr = (data.get("regeocode") or {}).get("formatted_address")
            if isinstance(addr, str) and addr:
                return addr
    except Exception as e:
        log.warning("regeo failed: %s", e)
    return "我的位置"
