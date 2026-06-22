"""Telegram 瑞幸点单机器人入口（长轮询）。

登录支持两种（取决于 P0 结论）：
  - Mini App：/start 给出 web_app 登录按钮（需配置 PUBLIC_BASE_URL）。
  - 粘贴 token 兜底：/login <token>。
两者都把 token 加密存进 SQLite，点单逻辑一致。
"""
from __future__ import annotations

import logging
from datetime import datetime

from telegram import ReplyKeyboardRemove, Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bot import flows, ui
from bot.agent import OrderingAgent
from bot.mcp_client import LuckinMCPClient
from core import db
from core.config import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bot")

MCP = LuckinMCPClient()
AGENT = OrderingAgent(MCP)


def _require_token(user_id: int):
    return db.get_token(user_id)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s = get_settings()
    kb = ui.login_keyboard(s.public_base_url)
    text = (
        "☕ 欢迎使用瑞幸点单助手！\n\n"
        "1) 先登录瑞幸账号"
        + ("（点下方按钮）" if kb else "：把你的 Token 发给我 `/login <token>`")
        + "\n2) 点「📍 发送我的位置」分享定位\n"
        "3) 直接说想喝什么，比如「来杯热的生椰拿铁」\n\n"
        "下单前我会显示价格让你确认，不会乱扣款 👍"
    )
    await update.message.reply_text(text, reply_markup=kb)
    await update.message.reply_text("分享位置 👇", reply_markup=ui.location_keyboard())


async def cmd_login(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    parts = (update.message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await update.message.reply_text("用法：/login <你的瑞幸Token>")
        return
    db.set_token(update.effective_user.id, parts[1].strip())
    await update.message.reply_text("✅ 登录成功，已安全保存。分享位置后就能点单啦～",
                                    reply_markup=ui.location_keyboard())


async def cmd_logout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db.delete_token(update.effective_user.id)
    context.user_data.clear()
    await update.message.reply_text("已退出登录，Token 已删除。")


async def on_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    loc = update.message.location
    context.user_data["location"] = (loc.longitude, loc.latitude)
    context.user_data["messages"] = AGENT.new_conversation((loc.longitude, loc.latitude))
    await update.message.reply_text("📍 位置已记录，想喝点什么？", reply_markup=ReplyKeyboardRemove())


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rec = _require_token(update.effective_user.id)
    if not rec:
        await update.message.reply_text("请先登录：/login <你的瑞幸Token>（或用 /start 的登录按钮）。")
        return
    messages = context.user_data.get("messages") or AGENT.new_conversation(context.user_data.get("location"))
    messages.append({"role": "user", "content": update.message.text})
    await context.bot.send_chat_action(update.effective_chat.id, "typing")
    try:
        result = await AGENT.step(messages, rec.token)
    except Exception as e:
        log.exception("agent step failed")
        await update.message.reply_text(f"出错了：{e}")
        return
    context.user_data["messages"] = result.messages

    if result.kind == "text":
        await update.message.reply_text(result.text or "（没听懂，换个说法试试？）")
        return

    # createOrder 拦截 → 价格确认护栏
    text, price = flows.format_preview(result.preview)
    reason = flows.spend_guard(update.effective_user.id, price)
    if reason:
        res2 = await AGENT.resume_after_confirm(result.messages, result.pending_call, rec.token,
                                                approved=False, exec_result={"rejected": reason})
        context.user_data["messages"] = res2.messages
        await update.message.reply_text("⛔ " + reason)
        if res2.text:
            await update.message.reply_text(res2.text)
        return
    context.user_data["pending"] = {"call": result.pending_call, "price": price}
    await update.message.reply_text(text + "\n\n确认下单吗？",
                                    reply_markup=ui.confirm_order_keyboard(price or 0.0))


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    pending = context.user_data.get("pending")
    messages = context.user_data.get("messages")
    rec = _require_token(q.from_user.id)
    if not pending or not rec or messages is None:
        await q.edit_message_text("会话已过期，请重新点单。")
        return

    if q.data == "order:cancel":
        res = await AGENT.resume_after_confirm(messages, pending["call"], rec.token, approved=False)
        context.user_data["messages"] = res.messages
        context.user_data.pop("pending", None)
        await q.edit_message_text("已取消本次下单。")
        if res.text:
            await q.message.reply_text(res.text)
        return

    # 确认 → 执行 createOrder（我们自己执行以拿到二维码并记账）
    await q.edit_message_text("⏳ 正在为你下单…")
    create_result = await AGENT.execute_pending(rec.token, pending["call"])
    text, qr, order_id, need_pay = flows.format_order_created(create_result)

    # 价格一致性兜底：实际下单价高于确认价（如优惠未生效）→ 显著告警
    confirmed = pending.get("price")
    actual = flows.created_price(create_result)
    if confirmed is not None and actual is not None and actual > confirmed + 0.01:
        await q.message.reply_text(
            f"⚠️ 注意：实际下单金额 ¥{actual:.2f} 高于确认价 ¥{confirmed:.2f}（优惠可能未生效）。"
            f"\n若还没支付，可在瑞幸 App 取消该订单。")
    record_price = actual if actual is not None else confirmed
    if order_id and record_price:
        db.record_spend(q.from_user.id, datetime.now().strftime("%Y-%m-%d"), record_price, order_id)
    await q.message.reply_text(text)
    if need_pay and qr:
        await context.bot.send_photo(q.message.chat_id, ui.make_qr_png(qr), caption="微信扫码支付")

    res = await AGENT.resume_after_confirm(messages, pending["call"], rec.token,
                                           approved=True, exec_result=create_result)
    context.user_data["messages"] = res.messages
    context.user_data.pop("pending", None)
    if res.text:
        await q.message.reply_text(res.text)

    if order_id:
        context.application.create_task(
            flows.poll_order_until_ready(context.bot, q.message.chat_id, MCP, rec.token, order_id)
        )


async def _post_init(app: Application) -> None:
    db.init_db()
    log.info("DB ready; bot started.")


def build_app() -> Application:
    s = get_settings()
    if not s.bot_token:
        raise SystemExit("BOT_TOKEN 未配置（.env）")
    app = ApplicationBuilder().token(s.bot_token).post_init(_post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("login", cmd_login))
    app.add_handler(CommandHandler("logout", cmd_logout))
    app.add_handler(MessageHandler(filters.LOCATION, on_location))
    app.add_handler(CallbackQueryHandler(on_callback, pattern=r"^order:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    return app


def main() -> None:
    build_app().run_polling()


if __name__ == "__main__":
    main()
