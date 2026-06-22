from bot import flows
from core import db

PREVIEW = {
    "code": 0, "msg": "success", "success": True,
    "data": {
        "discountPrice": 16, "privilegeMoney": 0,
        "shopInfo": {"deptName": "AI点单专用"},
        "productInfoList": [
            {"name": "耶加雪菲拿铁", "amount": 1, "additionDesc": "热", "estimatePrice": 16},
        ],
    },
}

CREATED = {
    "code": 0, "msg": "success", "success": True,
    "data": {
        "orderId": 7639308439653908490, "orderIdStr": "7639308439653908490",
        "payOrderQrCodeUrl": "https://opentest03.lkcoffee.com/transfer/qrcode?token=xxxx",
        "discountPrice": 16, "needPay": True,
    },
}

STATUS = {
    "code": 0, "msg": "success", "success": True,
    "data": {"orderStatus": 60, "orderStatusName": "等待取餐",
             "takeMealCodeInfo": {"code": "A123"}},
}


def test_unwrap():
    assert flows.unwrap(PREVIEW) == PREVIEW["data"]
    assert flows.unwrap({"foo": 1}) == {"foo": 1}


def test_format_preview():
    text, price = flows.format_preview(PREVIEW)
    assert price == 16.0
    assert "耶加雪菲拿铁" in text
    assert "合计应付：¥16.00" in text


def test_format_order_created():
    text, qr, order_id, need_pay = flows.format_order_created(CREATED)
    assert order_id == "7639308439653908490"
    assert need_pay is True
    assert qr and qr.startswith("https://")
    assert "已创建订单" in text


def test_format_order_created_no_pay():
    # 被券/余额全额覆盖：needPay=false → 免扫码，不返回二维码
    covered = {"success": True, "data": {"orderIdStr": "1", "needPay": False,
                                         "payOrderQrCodeUrl": "https://x/qr", "discountPrice": 0}}
    text, qr, order_id, need_pay = flows.format_order_created(covered)
    assert need_pay is False
    assert qr is None
    assert "无需扫码" in text


def test_format_order_status():
    assert "等待取餐" in flows.format_order_status(STATUS)
    assert "A123" in flows.format_order_status(STATUS)


def test_spend_guard():
    # limit is 100 (conftest). fresh user -> 50 ok, 150 blocked.
    assert flows.spend_guard(2001, 50.0) is None
    assert flows.spend_guard(2001, 150.0) is not None
    # accumulation pushes over the limit
    db.record_spend(2002, __import__("datetime").datetime.now().strftime("%Y-%m-%d"), 80.0, "o")
    assert flows.spend_guard(2002, 30.0) is not None  # 80 + 30 > 100
    assert flows.spend_guard(2002, 10.0) is None       # 80 + 10 <= 100
