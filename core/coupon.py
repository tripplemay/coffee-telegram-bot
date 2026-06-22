"""消费版 H5 (m.lkcoffee.com) 优惠券领取客户端 + 安全护栏。

逆向自前端 bundle（见 memory luckin-open-platform）。**铁律**：
  - 只走免费领取 `receiveCouponSend`，**绝不**调用 9.9 购卡的 `confirmOrder`。
  - `detail` 显示付费（discountedPrice>0 / 有 card scheme）的一律**跳过**——杜绝误花钱。
  - 响应出现风控/滑块/安全校验（validate / needSecurityVerify / REVIEW/RISK busiCode）→ **熔断**。
这是 ToS 灰色 + 封号风险的功能，必须 opt-in、低频、拟人；本模块只提供能力，限频/确认在调用方。
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from core import db

log = logging.getLogger("coupon")

# 限频（降封号风险）：每用户每日尝试上限 + 两次最小间隔
MAX_CLAIMS_PER_DAY = 3
MIN_CLAIM_INTERVAL_SEC = 3 * 3600

# 按天限频用固定 +08:00（中国），避免服务器若为 UTC 导致"天"在 08:00 重置、被绕过
_CST = timezone(timedelta(hours=8))


def today_cst() -> str:
    return datetime.now(_CST).strftime("%Y-%m-%d")

ORIGIN = "https://m.lkcoffee.com"
CAPI = ORIGIN + "/capi"
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1")

EP_VALIDCODE = "/resource/m/sys/base/validcode"
EP_LOGIN = "/resource/m/user/login"
EP_ENTER = "/resource/market/receiveCoupon/enter"
EP_DETAIL = "/resource/market/receiveCoupon/receiveCouponDetail"
EP_SEND = "/resource/market/receiveCoupon/receiveCouponSend"  # 唯一允许的写操作（免费领）


@dataclass
class ConsumerSession:
    """消费版登录态：cookie 罐 + csrf + member_id。可加密落库（to_json/from_json）。"""
    cookies: dict = field(default_factory=dict)
    csrf: str = ""
    member_id: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, s: str) -> "ConsumerSession":
        d = json.loads(s)
        return cls(d.get("cookies", {}) or {}, d.get("csrf", "") or "", d.get("member_id", "") or "")


# ---- 纯函数：安全判定（可单测，不触网）----

def is_paid_bag(detail_content: dict) -> bool:
    """receiveCouponDetail 是否为付费卡/券包 → 跳过，不去领它。

    主安全保证其实是上层：本模块**只调 receiveCouponSend（免费领）、永不调 confirmOrder（购买）**，
    且实测对付费 9.9 卡调 send 只回 sucSendCount:0、**不扣钱不下单**。本函数是二级护栏：避免对上卖卡做无谓调用。
    判定：① 有 card scheme 号 = 一定是付费卡（即便没带价）；② 价格能解析且 > 0 = 付费；③ 价格无法解析 = 保守跳过。
    """
    if not isinstance(detail_content, dict):
        return True  # 看不懂的结构，保守跳过
    if detail_content.get("masterCardSchemeNo") or detail_content.get("memberCardSchemeNo"):
        return True
    raw = detail_content.get("discountedPrice")
    if raw not in (None, ""):
        try:
            if float(raw) > 0:
                return True
        except (TypeError, ValueError):
            return True  # 价格字段存在但解析不了 → 保守跳过
    return False


def risk_blocked(resp: dict) -> bool:
    """响应是否出现风控/滑块/安全校验信号 → 调用方应立即熔断、停手。"""
    if not isinstance(resp, dict):
        return False
    content = resp.get("content")
    if isinstance(content, dict) and (content.get("validate") is True
                                      or content.get("needSecurityVerify") is True):
        return True
    busi = str(resp.get("busiCode") or "").upper()
    return "REVIEW" in busi or "RISK" in busi


def login_expired(resp: dict) -> bool:
    """响应是否表示消费版登录态已失效（需重新绑定）。"""
    if not isinstance(resp, dict):
        return False
    if resp.get("loginState") == 0:
        return True
    msg = str(resp.get("msg") or "")
    return "重新登录" in msg or "未登录" in msg


def claim_granted_count(send_resp: dict) -> int:
    """receiveCouponSend 实际发了几张券（sucSendCount）。SUCCESS 但为 0 = 没领到东西。"""
    content = (send_resp or {}).get("content") or {}
    if isinstance(content, dict):
        n = content.get("sucSendCount")
        if isinstance(n, int):
            return n
    return 0


def _base_headers() -> dict:
    return {"User-Agent": UA, "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9", "Origin": ORIGIN, "Referer": ORIGIN + "/"}


class ConsumerClient:
    """消费版 H5 会话客户端。每次操作从 ConsumerSession 重建 httpx 客户端、用完同步回 session。"""

    def __init__(self, session: Optional[ConsumerSession] = None) -> None:
        self.session = session or ConsumerSession()

    def _client(self) -> httpx.AsyncClient:
        c = httpx.AsyncClient(headers=_base_headers(), timeout=20, follow_redirects=True)
        for k, v in self.session.cookies.items():
            c.cookies.set(k, v, domain="m.lkcoffee.com", path="/")
        return c

    def _sync(self, c: httpx.AsyncClient) -> None:
        for k, v in c.cookies.items():
            self.session.cookies[k] = v

    async def _post(self, c: httpx.AsyncClient, path: str, body: dict) -> dict:
        csrf = self.session.csrf
        headers = {"x-csrf-token": csrf, "X-LK-MID": self.session.member_id,
                   "Content-Type": "application/json"}
        r = await c.post(f"{CAPI}{path}?_csrf={csrf}", json=body, headers=headers)
        self._sync(c)
        try:
            return r.json()
        except Exception:
            return {"_status": r.status_code, "_raw": r.text[:300]}

    async def start(self) -> None:
        """GET 首页，抓 csrf + 初始 cookie。"""
        async with self._client() as c:
            await c.get(ORIGIN + "/", headers={"Accept": "text/html,*/*"})
            self._sync(c)
            self.session.csrf = c.cookies.get("LK_prod_csrfToken") or self.session.csrf

    async def send_code(self, mobile: str, country_no: str = "86") -> dict:
        async with self._client() as c:
            return await self._post(c, EP_VALIDCODE, {"mobile": mobile, "countryNo": country_no})

    async def login(self, mobile: str, code: str, country_no: str = "86") -> dict:
        async with self._client() as c:
            resp = await self._post(c, EP_LOGIN,
                                    {"type": 1, "mobile": mobile, "countryNo": country_no,
                                     "validateCode": code})
        content = resp.get("content") if isinstance(resp, dict) else None
        if isinstance(content, dict):
            mid = content.get("memberId") or content.get("userId") or content.get("member_id")
            if mid:
                self.session.member_id = str(mid)
        return resp

    async def enter(self, dept_id: int = 0, type_: int = 4) -> dict:
        async with self._client() as c:
            return await self._post(c, EP_ENTER, {"deptId": dept_id, "type": type_})

    async def detail(self, dept_id: int, send_proposal_no: str) -> dict:
        async with self._client() as c:
            return await self._post(c, EP_DETAIL, {"sendProposalNo": send_proposal_no, "deptId": dept_id})

    async def _send_free(self, dept_id: int, send_proposal_no: str) -> dict:
        """唯一写操作：免费领取。仅 claim_weekly_free 在确认免费后调用。"""
        async with self._client() as c:
            return await self._post(c, EP_SEND,
                                    {"type": 1, "sendProposalNo": send_proposal_no, "deptId": dept_id})

    async def claim_weekly_free(self, dept_id: int = 0) -> dict:
        """安全编排：enter → detail →（付费/风控则跳过）→ 免费领取。返回结构化结果。

        返回 {ok, claimed:int, reason, paid:bool, blocked:bool}。绝不触发购买。
        """
        enter = await self.enter(dept_id)
        if login_expired(enter):
            return {"ok": False, "claimed": 0, "need_login": True, "reason": "领券登录已过期，请重新绑定"}
        if risk_blocked(enter):
            return {"ok": False, "claimed": 0, "blocked": True, "reason": "风控/校验，已熔断"}
        ec = (enter or {}).get("content") or {}
        spn = ec.get("sendProposalNo")
        if not ec.get("haveCouponBag") or not spn:
            return {"ok": True, "claimed": 0, "reason": "当前没有可领的福利券"}
        detail = await self.detail(dept_id, spn)
        if risk_blocked(detail):
            return {"ok": False, "claimed": 0, "blocked": True, "reason": "风控/校验，已熔断"}
        if is_paid_bag((detail or {}).get("content") or {}):
            return {"ok": True, "claimed": 0, "paid": True,
                    "reason": "本期是付费券包/卡，已跳过（不会扣钱）"}
        send = await self._send_free(dept_id, spn)
        if risk_blocked(send):
            return {"ok": False, "claimed": 0, "blocked": True, "reason": "风控/校验，已熔断"}
        n = claim_granted_count(send)
        if str(send.get("status")) == "SUCCESS" and n > 0:
            return {"ok": True, "claimed": n, "reason": f"已领取 {n} 张免费券"}
        return {"ok": True, "claimed": 0, "reason": "无免费券可领（本期为空）"}


def format_claim_result(res: dict) -> str:
    """把 run_claim_for_user 的结果转成给用户看的一句话（渠道共用）。need_login 由渠道单独处理。"""
    if res.get("rate_limited"):
        return "⏳ " + res.get("reason", "领得太勤啦，待会儿再试")
    if res.get("blocked"):
        return "🛑 " + res.get("reason", "触发风控") + "（已自动停手，保护你的账号）"
    if res.get("ok") is False:
        return "⚠️ " + res.get("reason", "领取失败，请稍后再试")
    if int(res.get("claimed", 0)) > 0:
        return "🎁 " + res.get("reason", "已领取免费券")
    if res.get("paid"):
        return "ℹ️ " + res.get("reason", "本期是付费券包，已跳过（不会扣钱）")
    return "🙂 " + res.get("reason", "本期暂无免费券可领，下周再来看看")


async def run_claim_for_user(user_key: int, day: str, now_ts: int) -> dict:
    """渠道无关的领券入口：限频 → 载入会话 → claim_weekly_free → 记账 + 回存会话。

    返回 dict（含 need_login / rate_limited / blocked / claimed / reason），渠道层据此措辞。
    绝不触发购买（claim_weekly_free 已保证）。
    """
    sess_json = db.get_consumer_session(user_key)
    if not sess_json:
        return {"need_login": True, "reason": "还没绑定领券登录"}
    if db.coupon_claims_today(user_key, day) >= MAX_CLAIMS_PER_DAY:
        return {"rate_limited": True,
                "reason": f"今日领取尝试已达上限（{MAX_CLAIMS_PER_DAY} 次），明天再来～"}
    last = db.last_coupon_claim_at(user_key)
    if last is not None and now_ts - last < MIN_CLAIM_INTERVAL_SEC:
        wait_min = (MIN_CLAIM_INTERVAL_SEC - (now_ts - last)) // 60
        return {"rate_limited": True,
                "reason": f"领得太勤啦，{wait_min} 分钟后再试（拟人限频，降低封号风险）"}

    client = ConsumerClient(ConsumerSession.from_json(sess_json))
    try:
        result = await client.claim_weekly_free(0)
    except Exception as e:  # 网络等异常：仍记一次尝试，防止异常态下被快速重试（限频/封号保护）
        log.warning("claim run failed for %s: %s", user_key, e)
        db.record_coupon_claim(user_key, day, 0)
        return {"ok": False, "reason": "领券服务暂时不可用，请稍后再试"}

    if not result.get("need_login"):
        db.record_coupon_claim(user_key, day, int(result.get("claimed", 0)))
        db.set_consumer_session(user_key, client.session.to_json())  # cookie 可能轮换 → 回存
    return result
