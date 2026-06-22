"""core/geo.py 坐标转换测试。"""
from __future__ import annotations

import math

from core.geo import wgs84_to_gcj02


def _dist_m(p, q) -> float:
    """两个 (lng, lat) 的粗略米距（小范围够用）。"""
    dlng = (p[0] - q[0]) * 111_320 * math.cos(math.radians(p[1]))
    dlat = (p[1] - q[1]) * 111_320
    return math.hypot(dlng, dlat)


def test_outside_china_passthrough():
    # 东京、伦敦：境外不加偏，原样返回
    assert wgs84_to_gcj02(139.6917, 35.6895) == (139.6917, 35.6895)
    assert wgs84_to_gcj02(-0.1276, 51.5072) == (-0.1276, 51.5072)


def test_beijing_offset_direction_and_magnitude():
    # 天安门 WGS-84 → GCJ-02：应向东北偏移，量级数百米（典型 ~500m）
    wgs = (116.397428, 39.90923)
    gcj = wgs84_to_gcj02(*wgs)
    assert gcj != wgs
    assert gcj[0] > wgs[0] and gcj[1] > wgs[1]          # 偏向东北
    d = _dist_m(wgs, gcj)
    assert 200 < d < 900, f"偏移量 {d:.0f}m 不在预期区间"


def test_known_reference_within_tolerance():
    # 与公开实现的参考值比对（容差 ~50m，吸收浮点/版本差异）
    gcj = wgs84_to_gcj02(116.397428, 39.90923)
    assert _dist_m(gcj, (116.403963, 39.910659)) < 50


def test_shanghai_in_china_offsets():
    wgs = (121.4737, 31.2304)  # 上海人民广场附近
    gcj = wgs84_to_gcj02(*wgs)
    assert 200 < _dist_m(wgs, gcj) < 900
