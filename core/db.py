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


def record_spend(tg_user_id: int, day: str, amount: float, order_id: Optional[str]) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO spend_log (tg_user_id, day, amount, order_id, created_at) VALUES (?, ?, ?, ?, ?)",
            (tg_user_id, day, amount, order_id, int(time.time())),
        )


def spend_today(tg_user_id: int, day: str) -> float:
    with _connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) AS s FROM spend_log WHERE tg_user_id=? AND day=?",
            (tg_user_id, day),
        ).fetchone()
    return float(row["s"] or 0.0)
