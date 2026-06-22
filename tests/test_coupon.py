"""core/coupon.py 安全护栏 + 领取编排测试（全程不触网）。"""
from __future__ import annotations

import asyncio

import time

from core import coupon, db
from core.coupon import (
    ConsumerClient,
    ConsumerSession,
    claim_granted_count,
    is_paid_bag,
    login_expired,
    risk_blocked,
)


def _run(coro):
    return asyncio.run(coro)


# ---- 纯安全判定 ----

def test_is_paid_bag():
    assert is_paid_bag({"discountedPrice": "9.9"}) is True
    assert is_paid_bag({"discountedPrice": 9.9}) is True            # 数字类型也判定
    assert is_paid_bag({"masterCardSchemeNo": "FF1"}) is True       # 有卡即付费（即便无价）
    assert is_paid_bag({"memberCardSchemeNo": "FFZ1"}) is True
    assert is_paid_bag({"masterCardSchemeNo": "FF1", "discountedPrice": None}) is True  # C1：有卡+无价 → 付费
    assert is_paid_bag({"discountedPrice": "abc"}) is True          # 解析不了 → 保守跳过
    assert is_paid_bag("not-a-dict") is True
    # 免费侧
    assert is_paid_bag({}) is False                                # 无价无卡 → 免费
    assert is_paid_bag({"discountedPrice": None}) is False          # 价缺省 + 无卡 → 免费
    assert is_paid_bag({"discountedPrice": "", "couponBagList": []}) is False
    assert is_paid_bag({"discountedPrice": "0.000"}) is False       # M1：数值 0 → 免费


def test_risk_blocked():
    assert risk_blocked({"content": {"validate": True}}) is True
    assert risk_blocked({"content": {"needSecurityVerify": True}}) is True
    assert risk_blocked({"busiCode": "REVIEW500"}) is True
    assert risk_blocked({"busiCode": "BASE000", "content": {"validate": False}}) is False


def test_claim_granted_count():
    assert claim_granted_count({"content": {"sucSendCount": 2}}) == 2
    assert claim_granted_count({"content": {"sucSendCount": 0}}) == 0
    assert claim_granted_count({"content": {}}) == 0


def test_session_roundtrip():
    s = ConsumerSession(cookies={"a": "1"}, csrf="x", member_id="42")
    s2 = ConsumerSession.from_json(s.to_json())
    assert s2.cookies == {"a": "1"} and s2.csrf == "x" and s2.member_id == "42"
    assert ConsumerSession.from_json("{}").cookies == {}


# ---- 编排：claim_weekly_free 绝不在付费/风控时领 ----

def _client_with(enter, detail=None, send=None):
    cl = ConsumerClient()
    cl.enter = enter
    if detail:
        cl.detail = detail
    if send:
        cl._send_free = send
    return cl


def test_claim_skips_paid_bag():
    async def enter(dept_id=0, type_=4):
        return {"content": {"haveCouponBag": 1, "sendProposalNo": "spn"}}

    async def detail(dept_id, spn):
        return {"content": {"discountedPrice": "9.9", "masterCardSchemeNo": "FF1"}}

    async def send(dept_id, spn):
        raise AssertionError("付费券包绝不能调用 send")

    res = _run(_client_with(enter, detail, send).claim_weekly_free(0))
    assert res["paid"] is True and res["claimed"] == 0 and res["ok"] is True


def test_claim_no_bag():
    async def enter(dept_id=0, type_=4):
        return {"content": {"haveCouponBag": 0}}

    res = _run(_client_with(enter).claim_weekly_free(0))
    assert res["claimed"] == 0 and "没有可领" in res["reason"]


def test_claim_risk_blocks_before_send():
    async def enter(dept_id=0, type_=4):
        return {"content": {"validate": True}}

    res = _run(_client_with(enter).claim_weekly_free(0))
    assert res["blocked"] is True and res["ok"] is False


def test_claim_free_success():
    async def enter(dept_id=0, type_=4):
        return {"content": {"haveCouponBag": 1, "sendProposalNo": "spn"}}

    async def detail(dept_id, spn):
        return {"content": {"discountedPrice": "", "couponBagList": [{"x": 1}]}}

    async def send(dept_id, spn):
        return {"status": "SUCCESS", "content": {"sucSendCount": 2}}

    res = _run(_client_with(enter, detail, send).claim_weekly_free(0))
    assert res["claimed"] == 2 and res["ok"] is True


def test_claim_free_but_empty():
    async def enter(dept_id=0, type_=4):
        return {"content": {"haveCouponBag": 1, "sendProposalNo": "spn"}}

    async def detail(dept_id, spn):
        return {"content": {"discountedPrice": ""}}

    async def send(dept_id, spn):
        return {"status": "SUCCESS", "content": {"sucSendCount": 0}}

    res = _run(_client_with(enter, detail, send).claim_weekly_free(0))
    assert res["claimed"] == 0 and "无免费券" in res["reason"]


# ---- db：消费版会话存储 + 领券限频日志 ----

def test_consumer_session_storage():
    assert db.get_consumer_session(8801) is None
    db.set_consumer_session(8801, '{"csrf":"abc"}')
    assert db.get_consumer_session(8801) == '{"csrf":"abc"}'
    db.set_consumer_session(8801, '{"csrf":"def"}')  # upsert
    assert db.get_consumer_session(8801) == '{"csrf":"def"}'
    db.delete_consumer_session(8801)
    assert db.get_consumer_session(8801) is None


def test_coupon_claim_log():
    assert db.coupon_claims_today(8802, "2026-06-22") == 0
    assert db.last_coupon_claim_at(8802) is None
    db.record_coupon_claim(8802, "2026-06-22", 1)
    db.record_coupon_claim(8802, "2026-06-22", 0)
    assert db.coupon_claims_today(8802, "2026-06-22") == 2
    assert db.coupon_claims_today(8802, "2026-06-23") == 0
    assert isinstance(db.last_coupon_claim_at(8802), int)


# ---- 渠道无关 runner：限频 / 需登录 / 成功 ----

def test_login_expired():
    assert login_expired({"loginState": 0}) is True
    assert login_expired({"msg": "请重新登录"}) is True
    assert login_expired({"loginState": 1, "msg": "成功"}) is False


def test_run_claim_need_login_when_no_session():
    res = _run(coupon.run_claim_for_user(9901, "2026-06-22", 1000))
    assert res["need_login"] is True


def test_run_claim_rate_limited_daily():
    db.set_consumer_session(9902, ConsumerSession(csrf="x").to_json())
    for _ in range(coupon.MAX_CLAIMS_PER_DAY):
        db.record_coupon_claim(9902, "2026-06-22", 0)
    res = _run(coupon.run_claim_for_user(9902, "2026-06-22", 10_000_000_000))
    assert res["rate_limited"] is True


def test_run_claim_min_interval():
    db.set_consumer_session(9903, ConsumerSession(csrf="x").to_json())
    db.record_coupon_claim(9903, "2026-06-22", 0)  # created_at ≈ now
    res = _run(coupon.run_claim_for_user(9903, "2026-06-22", int(time.time())))
    assert res["rate_limited"] is True


def test_run_claim_success_records_and_saves(monkeypatch):
    db.set_consumer_session(9904, ConsumerSession(csrf="x", cookies={"a": "1"}).to_json())

    async def fake_claim(self, dept_id=0):
        self.session.cookies["rotated"] = "1"  # 模拟 cookie 轮换
        return {"ok": True, "claimed": 1, "reason": "已领取 1 张免费券"}

    monkeypatch.setattr(coupon.ConsumerClient, "claim_weekly_free", fake_claim)
    res = _run(coupon.run_claim_for_user(9904, "2026-06-22", 10_000_000_000))
    assert res["claimed"] == 1
    assert db.coupon_claims_today(9904, "2026-06-22") == 1
    assert "rotated" in db.get_consumer_session(9904)  # 会话已回存
