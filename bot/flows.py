"""下单流程辅助：解析瑞幸响应、消费护栏、订单状态轮询。"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Optional

from telegram import Bot

from bot.mcp_client import LuckinMCPClient
from core import db
from core.config import get_settings
from core.luckin import ORDER_STATUS

log = logging.getLogger("flows")

# 视为终态的订单状态（停止轮询）
_TERMINAL_STATUS = {60, 80, 100}  # 等待取餐 / 已完成 / 已取消


def unwrap(resp: Any) -> Any:
    """MCP 工具返回多为 {code,msg,data,success} 信封；取出 data。否则原样返回。"""
    if isinstance(resp, dict) and "data" in resp and ("success" in resp or "code" in resp):
        return resp.get("data")
    return resp


def _price_of(preview_data: dict) -> Optional[float]:
    for key in ("discountPrice", "orderPayAmount", "totalInitialPrice"):
        v = preview_data.get(key)
        if isinstance(v, (int, float)):
            return float(v)
    return None


def format_preview(resp: Any) -> tuple[str, Optional[float]]:
    data = unwrap(resp)
    if not isinstance(data, dict):
        return (f"预览失败：{resp}", None)
    price = _price_of(data)
    lines = ["🧾 订单预览"]
    shop = data.get("shopInfo") or {}
    if shop.get("deptName"):
        lines.append(f"门店：{shop['deptName']}")
    for it in data.get("productInfoList") or []:
        name = it.get("name", "商品")
        amount = it.get("amount", 1)
        extra = it.get("additionDesc") or ""
        ep = it.get("estimatePrice")
        seg = f"• {name} ×{amount}"
        if extra:
            seg += f"（{extra}）"
        if isinstance(ep, (int, float)):
            seg += f"  ¥{ep}"
        lines.append(seg)
    priv = data.get("privilegeMoney")
    if isinstance(priv, (int, float)) and priv > 0:
        lines.append(f"优惠：-¥{priv}")
    if price is not None:
        lines.append(f"合计应付：¥{price:.2f}")
    return ("\n".join(lines), price)


def spend_guard(tg_user_id: int, price: Optional[float]) -> Optional[str]:
    """返回 None 表示放行；否则返回拒绝原因。"""
    if price is None:
        return None
    limit = get_settings().daily_spend_limit
    day = datetime.now().strftime("%Y-%m-%d")
    already = db.spend_today(tg_user_id, day)
    if already + price > limit:
        return f"超出单日消费上限（已花 ¥{already:.2f}，本单 ¥{price:.2f}，上限 ¥{limit:.0f}）。"
    return None


def format_order_created(resp: Any) -> tuple[str, Optional[str], Optional[str], bool]:
    """返回 (文本, 支付二维码内容, orderId, 是否需要扫码支付)。

    若 needPay=false（被咖啡库券/余额全额覆盖），走免扫码路径：不返回二维码。
    """
    data = unwrap(resp)
    if not isinstance(data, dict):
        return (f"下单失败：{resp}", None, None, False)
    order_id = data.get("orderIdStr") or (str(data["orderId"]) if data.get("orderId") else None)
    need_pay = bool(data.get("needPay"))
    qr = (data.get("payOrderQrCodeUrl") or data.get("payOrderUrl")) if need_pay else None
    price = data.get("discountPrice")
    text = "✅ 已创建订单"
    if isinstance(price, (int, float)):
        text += f"，应付 ¥{price}"
    if need_pay:
        text += "\n请扫下方二维码用微信支付 👇"
    else:
        text += "\n🎉 已用券/余额完成支付，无需扫码，正在为你制作 ☕"
    return (text, qr, order_id, need_pay)


def created_price(resp: Any) -> Optional[float]:
    """createOrder 实际应付价（discountPrice）。"""
    data = unwrap(resp)
    if isinstance(data, dict):
        v = data.get("discountPrice")
        if isinstance(v, (int, float)):
            return float(v)
    return None


def format_order_status(resp: Any) -> str:
    data = unwrap(resp)
    if not isinstance(data, dict):
        return f"查询失败：{resp}"
    status = data.get("orderStatus")
    name = data.get("orderStatusName") or ORDER_STATUS.get(status, str(status))
    lines = [f"📦 订单状态：{name}"]
    take = (data.get("takeMealCodeInfo") or {}).get("code")
    if take and take != "生成中":
        lines.append(f"取餐码：{take}")
    return "\n".join(lines)


async def poll_order_until_ready(bot: Bot, chat_id: int, mcp: LuckinMCPClient, token: str,
                                 order_id: str, interval: int = 20, max_minutes: int = 30) -> None:
    """后台轮询订单状态，状态变化时推送；到终态或超时停止。"""
    last_status = None
    deadline = max_minutes * 60
    waited = 0
    while waited < deadline:
        await asyncio.sleep(interval)
        waited += interval
        try:
            resp = await mcp.call_tool(token, "queryOrderDetailInfo", {"orderId": order_id})
        except Exception as e:
            log.warning("poll order %s failed: %s", order_id, e)
            continue
        data = unwrap(resp)
        if not isinstance(data, dict):
            continue
        status = data.get("orderStatus")
        if status != last_status:
            last_status = status
            try:
                await bot.send_message(chat_id, format_order_status(resp))
            except Exception as e:
                log.warning("push status failed: %s", e)
        if status in _TERMINAL_STATUS:
            return
