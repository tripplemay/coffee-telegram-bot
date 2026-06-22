import sqlite3

from core import db
from core.config import get_settings


def test_token_roundtrip_and_encrypted():
    db.set_token(1001, "tok-secret-xyz", token_date="2026-07-22")
    rec = db.get_token(1001)
    assert rec is not None
    assert rec.token == "tok-secret-xyz"
    assert rec.token_date == "2026-07-22"

    # stored blob must NOT contain the plaintext token
    raw = sqlite3.connect(get_settings().db_path).execute(
        "SELECT enc_token FROM user_tokens WHERE tg_user_id=1001"
    ).fetchone()[0]
    assert b"tok-secret-xyz" not in raw


def test_delete_token():
    db.set_token(1002, "tok2")
    db.delete_token(1002)
    assert db.get_token(1002) is None


def test_spend_tracking():
    db.record_spend(1003, "2026-06-22", 16.0, "o1")
    db.record_spend(1003, "2026-06-22", 13.5, "o2")
    db.record_spend(1003, "2026-06-23", 99.0, "o3")
    assert db.spend_today(1003, "2026-06-22") == 29.5
    assert db.spend_today(1003, "2026-06-23") == 99.0
    assert db.spend_today(9999, "2026-06-22") == 0.0
