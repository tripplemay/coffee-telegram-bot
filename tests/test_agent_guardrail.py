"""核心安全测试：createOrder 必须被拦截，绝不在 agent 循环里自动执行。"""
import json

import pytest

from bot.agent import OrderingAgent


class FakeMCP:
    """记录所有工具调用，返回 canned 信封。"""
    def __init__(self):
        self.calls = []
        self.calls_full = []

    async def call_tool(self, token, name, arguments):
        self.calls.append(name)
        self.calls_full.append((name, arguments))
        if name == "queryShopList":
            return {"success": True, "data": [{"deptId": 245062453, "deptName": "AI点单专用"}]}
        if name == "searchProductForMcp":
            return {"success": True, "data": [{"productId": 11447, "skuCode": "SP9636-00001", "productName": "耶加雪菲拿铁", "estimatePrice": 16}]}
        if name == "previewOrder":
            return {"success": True, "data": {"discountPrice": 12.45, "privilegeMoney": 5.55, "totalInitialPrice": 18,
                                              "couponCodeList": ["SY-TEST-COUPON"],
                                              "productInfoList": [{"name": "耶加雪菲拿铁", "amount": 1, "estimatePrice": 12.45}]}}
        if name == "createOrder":
            return {"success": True, "data": {"orderIdStr": "999", "payOrderQrCodeUrl": "https://x/qr", "needPay": True, "discountPrice": 12.45}}
        return {"success": True, "data": {}}


def _assistant_tool_call(name, args):
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": f"call_{name}", "type": "function",
         "function": {"name": name, "arguments": json.dumps(args)}}
    ]}


def _script(*messages):
    """返回一个可替换 _chat 的 async 函数，按顺序吐出脚本消息。"""
    it = iter(messages)

    async def fake_chat(_messages):
        return next(it)
    return fake_chat


@pytest.mark.asyncio
async def test_create_order_is_intercepted(monkeypatch):
    mcp = FakeMCP()
    agent = OrderingAgent(mcp)  # type: ignore[arg-type]

    monkeypatch.setattr(agent, "_chat", _script(
        _assistant_tool_call("queryShopList", {"longitude": 116.39, "latitude": 39.98}),
        _assistant_tool_call("searchProductForMcp", {"deptId": 245062453, "query": "生椰拿铁"}),
        _assistant_tool_call("createOrder", {
            "deptId": 245062453,
            "productList": [{"amount": 1, "productId": 11447, "skuCode": "SP9636-00001"}],
            "longitude": 116.39, "latitude": 39.98,
        }),
        {"role": "assistant", "content": "已下单成功，取餐码稍后生成。", "tool_calls": None},
    ))

    msgs = agent.new_conversation((116.39, 39.98))
    msgs.append({"role": "user", "content": "来杯热的生椰拿铁"})
    result = await agent.step(msgs, token="fake-token")

    # 关键断言：停在确认态，且 createOrder 从未被执行
    assert result.kind == "confirm"
    assert "createOrder" not in mcp.calls
    assert mcp.calls == ["queryShopList", "searchProductForMcp", "previewOrder"]
    text, price = __import__("bot.flows", fromlist=["format_preview"]).format_preview(result.preview)
    assert price == 12.45

    # 用户确认后才执行 createOrder
    create_result = await agent.execute_pending("fake-token", result.pending_call)
    assert "createOrder" in mcp.calls
    res2 = await agent.resume_after_confirm(result.messages, result.pending_call,
                                            "fake-token", approved=True, exec_result=create_result)
    assert res2.kind == "text"
    assert "下单成功" in res2.text


@pytest.mark.asyncio
async def test_preview_coupons_injected_into_create_order(monkeypatch):
    """预览自动匹配的券必须注入 createOrder，确保实付=确认价（不按面价扣款）。"""
    mcp = FakeMCP()
    agent = OrderingAgent(mcp)  # type: ignore[arg-type]
    monkeypatch.setattr(agent, "_chat", _script(
        # LLM 调 createOrder 时没带 couponCodeList（常见情况）
        _assistant_tool_call("createOrder", {
            "deptId": 245062453,
            "productList": [{"amount": 1, "productId": 11447, "skuCode": "SP9636-00001"}],
            "longitude": 116.39, "latitude": 39.98,
        }),
        {"role": "assistant", "content": "下单成功。", "tool_calls": None},
    ))
    msgs = agent.new_conversation((116.39, 39.98))
    msgs.append({"role": "user", "content": "下单"})
    result = await agent.step(msgs, token="t")
    assert result.kind == "confirm"

    # 确认后执行 createOrder：参数里必须已注入预览的券
    await agent.execute_pending("t", result.pending_call)
    create_args = next(args for name, args in mcp.calls_full if name == "createOrder")
    assert create_args.get("couponCodeList") == ["SY-TEST-COUPON"]


@pytest.mark.asyncio
async def test_cancel_does_not_create_order(monkeypatch):
    mcp = FakeMCP()
    agent = OrderingAgent(mcp)  # type: ignore[arg-type]
    monkeypatch.setattr(agent, "_chat", _script(
        _assistant_tool_call("createOrder", {
            "deptId": 1, "productList": [{"amount": 1, "productId": 11447, "skuCode": "SP9636-00001"}],
            "longitude": 1.0, "latitude": 2.0,
        }),
        {"role": "assistant", "content": "好的，已取消。", "tool_calls": None},
    ))
    msgs = agent.new_conversation()
    msgs.append({"role": "user", "content": "下单"})
    result = await agent.step(msgs, token="t")
    assert result.kind == "confirm"

    res2 = await agent.resume_after_confirm(result.messages, result.pending_call, "t", approved=False)
    assert res2.kind == "text"
    assert "createOrder" not in mcp.calls  # 取消路径绝不下单
