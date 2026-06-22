"""SQLite + Fernet 加密的 Token 存储（按 Telegram 用户）。

设计为不可变风格：读返回新 dict，不就地修改持久层之外的状态。
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from core.config import get_settings


@dataclass(frozen=True)
class TokenRecord:
    tg_user_id: int
    token: str
    token_date: Optional[str]  # 瑞幸返回的 luckyMcpTokenDate（到期信息）
    updated_at: int


_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_tokens (
    tg_user_id  INTEGER PRIMARY KEY,
    enc_token   BLOB    NOT NULL,
    token_date  TEXT,
    updated_at  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS spend_log (
    tg_user_id  INTEGER NOT NULL,
    day         TEXT    NOT NULL,   -- YYYY-MM-DD
    amount      REAL    NOT NULL,
    order_id    TEXT,
    created_at  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
    tg_user_id    INTEGER NOT NULL,
    order_id      TEXT    NOT NULL,
    summary       TEXT,
    created_at    INTEGER NOT NULL,
    cancelled_at  INTEGER,
    PRIMARY KEY (tg_user_id, order_id)
);
CREATE TABLE IF NOT EXISTS user_location (
    tg_user_id  INTEGER PRIMARY KEY,
    lng         REAL    NOT NULL,
    lat         REAL    NOT NULL,
    label       TEXT,
    updated_at  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS login_nonce (
    nonce       TEXT    PRIMARY KEY,
    user_key    INTEGER NOT NULL,
    channel     TEXT,                 -- 'tg' | 'wx'：登录成功后往哪个渠道回推
    push_target TEXT,                 -- 原生推送目标（tg=chat_id，wx=原始 user_key 串）
    created_at  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS consumer_session (
    user_key    INTEGER PRIMARY KEY,  -- 消费版 H5 (m.lkcoffee.com) 登录态，用于优惠券领取
    enc_session BLOB    NOT NULL,      -- Fernet 加密的 ConsumerSession JSON
    updated_at  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS coupon_claim_log (
    user_key    INTEGER NOT NULL,     -- 领券限频用：每用户每日次数 + 最近一次时间
    day         TEXT    NOT NULL,
    claimed     INTEGER NOT NULL,
    created_at  INTEGER NOT NULL
);
"""


def _fernet() -> Fernet:
    key = get_settings().fernet_key
    if not key:
        raise RuntimeError("FERNET_KEY 未配置；生成: "
                           'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"')
    return Fernet(key.encode() if isinstance(key, str) else key)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(get_settings().db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(_SCHEMA)
        # 轻量迁移：旧库的 orders 表补 cancelled_at 列
        cols = [r[1] for r in conn.execute("PRAGMA table_info(orders)").fetchall()]
        if "cancelled_at" not in cols:
            conn.execute("ALTER TABLE orders ADD COLUMN cancelled_at INTEGER")
        # 轻量迁移：旧库的 login_nonce 表补 channel / push_target 列（登录成功回推用）
        ncols = [r[1] for r in conn.execute("PRAGMA table_info(login_nonce)").fetchall()]
        if "channel" not in ncols:
            conn.execute("ALTER TABLE login_nonce ADD COLUMN channel TEXT")
        if "push_target" not in ncols:
            conn.execute("ALTER TABLE login_nonce ADD COLUMN push_target TEXT")


def set_token(tg_user_id: int, token: str, token_date: Optional[str] = None) -> None:
    enc = _fernet().encrypt(token.encode())
    with _connect() as conn:
        conn.execute(
            "INSERT INTO user_tokens (tg_user_id, enc_token, token_date, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(tg_user_id) DO UPDATE SET "
            "enc_token=excluded.enc_token, token_date=excluded.token_date, updated_at=excluded.updated_at",
            (tg_user_id, enc, token_date, int(time.time())),
        )


def get_token(tg_user_id: int) -> Optional[TokenRecord]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT tg_user_id, enc_token, token_date, updated_at FROM user_tokens WHERE tg_user_id=?",
            (tg_user_id,),
        ).fetchone()
    if not row:
        return None
    try:
        token = _fernet().decrypt(row["enc_token"]).decode()
    except InvalidToken:
        return None  # 密钥变更 / 数据损坏 → 视为未登录
    return TokenRecord(row["tg_user_id"], token, row["token_date"], row["updated_at"])


def delete_token(tg_user_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM user_tokens WHERE tg_user_id=?", (tg_user_id,))


def set_consumer_session(user_key: int, session_json: str) -> None:
    """加密保存消费版 H5 登录态（优惠券领取用）。"""
    enc = _fernet().encrypt(session_json.encode())
    with _connect() as conn:
        conn.execute(
            "INSERT INTO consumer_session (user_key, enc_session, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(user_key) DO UPDATE SET enc_session=excluded.enc_session, updated_at=excluded.updated_at",
            (user_key, enc, int(time.time())),
        )


def get_consumer_session(user_key: int) -> Optional[str]:
    """取回消费版登录态 JSON；无/损坏返回 None。"""
    with _connect() as conn:
        row = conn.execute(
            "SELECT enc_session FROM consumer_session WHERE user_key=?", (user_key,)
        ).fetchone()
    if not row:
        return None
    try:
        return _fernet().decrypt(row["enc_session"]).decode()
    except InvalidToken:
        return None


def delete_consumer_session(user_key: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM consumer_session WHERE user_key=?", (user_key,))


def record_coupon_claim(user_key: int, day: str, claimed: int) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO coupon_claim_log (user_key, day, claimed, created_at) VALUES (?, ?, ?, ?)",
            (user_key, day, claimed, int(time.time())),
        )


def coupon_claims_today(user_key: int, day: str) -> int:
    """今日已发起的领取次数（限频用，含领到 0 张的尝试）。"""
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM coupon_claim_log WHERE user_key=? AND day=?", (user_key, day)
        ).fetchone()
    return int(row["n"] or 0)


def last_coupon_claim_at(user_key: int) -> Optional[int]:
    """最近一次领取尝试的时间戳（最小间隔限频用）；从未则 None。"""
    with _connect() as conn:
        row = conn.execute(
            "SELECT MAX(created_at) AS t FROM coupon_claim_log WHERE user_key=?", (user_key,)
        ).fetchone()
    return int(row["t"]) if row and row["t"] is not None else None


def record_spend(tg_user_id: int, day: str, amount: float, order_id: Optional[str]) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO spend_log (tg_user_id, day, amount, order_id, created_at) VALUES (?, ?, ?, ?, ?)",
            (tg_user_id, day, amount, order_id, int(time.time())),
        )


def record_order(tg_user_id: int, order_id: str, summary: Optional[str] = None) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO orders (tg_user_id, order_id, summary, created_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(tg_user_id, order_id) DO UPDATE SET "
            "summary=COALESCE(excluded.summary, orders.summary)",  # 后来的 NULL 摘要不抹掉已有标签
            (tg_user_id, order_id, summary, int(time.time())),
        )


def mark_order_cancelled(tg_user_id: int, order_id: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE orders SET cancelled_at=? WHERE tg_user_id=? AND order_id=?",
            (int(time.time()), tg_user_id, order_id),
        )


def list_orders(tg_user_id: int, limit: int = 5) -> list[dict]:
    """最近未取消的订单（最新在前）。仅含经本 bot 创建的单。"""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT order_id, summary, created_at FROM orders "
            "WHERE tg_user_id=? AND cancelled_at IS NULL "
            "ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (tg_user_id, limit),
        ).fetchall()
    return [{"order_id": r["order_id"], "summary": r["summary"], "created_at": r["created_at"]} for r in rows]


@dataclass(frozen=True)
class NonceRecord:
    user_key: int
    channel: Optional[str]       # 'tg' | 'wx'：回推到哪个渠道
    push_target: Optional[str]   # 原生推送目标（tg=chat_id，wx=原始 user_key 串）


def create_login_nonce(nonce: str, user_key: int, channel: Optional[str] = None,
                       push_target: Optional[str] = None) -> None:
    """登录页用：把一次性登录链接绑定到 bot 用户(已折算成 db key)。

    channel/push_target 用于登录成功后把"✅ 已登录"回推到来源渠道（见 core/push.py）。
    """
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO login_nonce (nonce, user_key, channel, push_target, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (nonce, user_key, channel, push_target, int(time.time())),
        )


def consume_login_nonce(nonce: str, max_age: int = 900) -> Optional[NonceRecord]:
    """取出并删除 nonce，返回绑定信息（单次、默认 15 分钟内有效）。过期/不存在返回 None。"""
    with _connect() as conn:
        row = conn.execute(
            "SELECT user_key, channel, push_target, created_at FROM login_nonce WHERE nonce=?", (nonce,)
        ).fetchone()
        if row:
            conn.execute("DELETE FROM login_nonce WHERE nonce=?", (nonce,))
    if not row or int(time.time()) - row["created_at"] > max_age:
        return None
    return NonceRecord(row["user_key"], row["channel"], row["push_target"])


def peek_login_nonce(nonce: str, max_age: int = 900) -> bool:
    """只校验 nonce 是否存在且未过期（不删除）。用于发短信前的轻量准入，防 SMS 滥用。"""
    if not nonce:
        return False
    with _connect() as conn:
        row = conn.execute("SELECT created_at FROM login_nonce WHERE nonce=?", (nonce,)).fetchone()
    return bool(row) and int(time.time()) - row["created_at"] <= max_age


def set_location(tg_user_id: int, lng: float, lat: float, label: Optional[str] = None) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO user_location (tg_user_id, lng, lat, label, updated_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(tg_user_id) DO UPDATE SET "
            "lng=excluded.lng, lat=excluded.lat, label=excluded.label, updated_at=excluded.updated_at",
            (tg_user_id, lng, lat, label, int(time.time())),
        )


def get_location(tg_user_id: int) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT lng, lat, label FROM user_location WHERE tg_user_id=?", (tg_user_id,)
        ).fetchone()
    return {"lng": row["lng"], "lat": row["lat"], "label": row["label"]} if row else None


def spend_today(tg_user_id: int, day: str) -> float:
    with _connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) AS s FROM spend_log WHERE tg_user_id=? AND day=?",
            (tg_user_id, day),
        ).fetchone()
    return float(row["s"] or 0.0)
