"""Telegram 键盘/按钮/二维码 构造。"""
from __future__ import annotations

from io import BytesIO
from typing import Optional

import qrcode
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    WebAppInfo,
)


def login_keyboard(public_base_url: str) -> Optional[InlineKeyboardMarkup]:
    """Mini App 登录按钮（web_app 按钮无需 BotFather 注册域名）。未配置域名则返回 None。"""
    if not public_base_url:
        return None
    url = public_base_url.rstrip("/") + "/login"
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔑 登录瑞幸", web_app=WebAppInfo(url=url))]])


def location_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton("📍 发送我的位置", request_location=True)]],
        resize_keyboard=True, one_time_keyboard=True,
    )


def confirm_order_keyboard(price: float) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(f"✅ 确认支付 ¥{price:.2f}", callback_data="order:confirm"),
        InlineKeyboardButton("❌ 取消", callback_data="order:cancel"),
    ]])


def make_qr_png(data: str) -> BytesIO:
    img = qrcode.make(data)
    buf = BytesIO()
    buf.name = "pay_qr.png"
    img.save(buf, format="PNG")
    buf.seek(0)  # 必须 rewind，否则 Telegram 收到 0 字节
    return buf
