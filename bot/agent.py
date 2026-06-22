"""LLM 点单 agent：OpenAI 兼容（aigc-gateway）function-calling 循环。

关键安全设计：`createOrder`（花真钱）永不由 agent 自动执行。当模型要下单时，
循环**暂停**并返回 ConfirmRequired，把 previewOrder 明细交给 Telegram 层让用户点
按钮确认；确认后再 resume 执行 createOrder 并续聊。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from bot.mcp_client import LuckinMCPClient, MCPToolError
from bot.tools import CONFIRM_REQUIRED, TOOL_SCHEMAS
from core.config import get_settings

log = logging.getLogger("agent")

SYSTEM_PROMPT = """你是 Telegram 上的瑞幸咖啡点单助手，用简体中文、简洁口语化地帮用户点单。

可用工具覆盖：查门店(queryShopList)、搜商品(searchProductForMcp)、切换属性(switchProduct)、
商品详情(queryProductDetailInfo)、订单预览(previewOrder)、创建订单(createOrder)、
订单详情(queryOrderDetailInfo)、取消订单(cancelOrder)。

规则：
1. 必须先有门店：用用户的经纬度调 queryShopList，选定 deptId 后再搜商品。若没有位置信息，请让用户分享位置。
2. 商品的 productId / skuCode 一律来自 searchProductForMcp 或 switchProduct 的返回，绝不要编造。
3. 下单前先调 previewOrder 给用户看明细。调用 createOrder 时，系统会自动弹出价格确认按钮让用户点“确认”——你只管在合适时机调用 createOrder 即可，不要自己假装已下单。
4. 拿到订单后可用 queryOrderDetailInfo 查状态/取餐码。
5. 回答简短，不要罗列大段 JSON。金额、温度、杯型等关键信息要讲清楚。
"""


@dataclass
class AgentResult:
    kind: str  # "text" | "confirm"
    text: str = ""
    # kind == "confirm":
    pending_call: Optional[dict] = None  # 待执行的 createOrder tool_call
    preview: Any = None                  # previewOrder 明细
    messages: Optional[list] = None      # 续聊用的对话状态


class OrderingAgent:
    def __init__(self, mcp: LuckinMCPClient, http: Optional[httpx.AsyncClient] = None) -> None:
        s = get_settings()
        self._mcp = mcp
        self._model = s.llm_model
        self._url = f"{s.aigc_base_url}/chat/completions"
        self._key = s.aigc_api_key
        self._http = http or httpx.AsyncClient(timeout=60.0)

    def new_conversation(self, location: Optional[tuple[float, float]] = None) -> list[dict]:
        sys = SYSTEM_PROMPT
        if location:
            sys += f"\n\n当前用户位置：经度 {location[0]}，纬度 {location[1]}。"
        return [{"role": "system", "content": sys}]

    async def _chat(self, messages: list[dict]) -> dict:
        body = {"model": self._model, "messages": messages,
                "tools": TOOL_SCHEMAS, "tool_choice": "auto"}
        r = await self._http.post(self._url, headers={"Authorization": f"Bearer {self._key}"}, json=body)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]

    async def _dispatch(self, token: str, name: str, args_json: str) -> Any:
        try:
            args = json.loads(args_json or "{}")
        except json.JSONDecodeError:
            return {"error": f"参数解析失败: {args_json!r}"}
        try:
            return await self._mcp.call_tool(token, name, args)
        except MCPToolError as e:
            return {"error": str(e)}

    async def step(self, messages: list[dict], token: str, max_iters: int = 8) -> AgentResult:
        """推进对话直到产生文本回复，或遇到 createOrder 需要用户确认。"""
        for _ in range(max_iters):
            msg = await self._chat(messages)
            messages.append(msg)
            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                return AgentResult("text", text=msg.get("content") or "", messages=messages)

            confirm_call = None
            for tc in tool_calls:
                name = tc["function"]["name"]
                if name in CONFIRM_REQUIRED and confirm_call is None:
                    confirm_call = tc  # 第一个 createOrder 留给人工确认
                    continue
                # 其余（含多余的 createOrder）立即执行 / 回绝
                if name in CONFIRM_REQUIRED:
                    result = {"error": "一次只能确认一单，请重试"}
                else:
                    result = await self._dispatch(token, name, tc["function"].get("arguments", "{}"))
                messages.append({"role": "tool", "tool_call_id": tc["id"],
                                 "content": json.dumps(result, ensure_ascii=False)})

            if confirm_call is not None:
                preview = await self._preview_for(token, confirm_call)
                confirm_call = self._with_preview_coupons(confirm_call, preview)
                return AgentResult("confirm", pending_call=confirm_call, preview=preview, messages=messages)
        return AgentResult("text", text="（处理步骤过多，已停止；请换个说法重试）", messages=messages)

    @staticmethod
    def _with_preview_coupons(call: dict, preview: Any) -> dict:
        """把 previewOrder 自动匹配到的 couponCodeList 注入 createOrder 参数。

        previewOrder 的优惠价依赖它自动匹配的券；这些券必须显式传给 createOrder，
        否则会按面价扣款，导致确认价与实付价不一致。返回新 call（不就地修改）。
        """
        data = preview.get("data") if isinstance(preview, dict) and "data" in preview else preview
        coupons = data.get("couponCodeList") if isinstance(data, dict) else None
        if not coupons:
            return call
        try:
            args = json.loads(call["function"].get("arguments", "{}"))
        except json.JSONDecodeError:
            return call
        args["couponCodeList"] = coupons
        return {**call, "function": {**call["function"], "arguments": json.dumps(args, ensure_ascii=False)}}

    async def _preview_for(self, token: str, create_call: dict) -> Any:
        try:
            args = json.loads(create_call["function"].get("arguments", "{}"))
        except json.JSONDecodeError:
            return {"error": "createOrder 参数解析失败"}
        if "deptId" in args and "productList" in args:
            return await self._dispatch(token, "previewOrder",
                                        json.dumps({"deptId": args["deptId"], "productList": args["productList"]}))
        return {"error": "缺少 deptId/productList"}

    async def execute_pending(self, token: str, pending_call: dict) -> Any:
        """执行被确认的工具调用（通常是 createOrder），返回原始结果。"""
        return await self._dispatch(token, pending_call["function"]["name"],
                                    pending_call["function"].get("arguments", "{}"))

    async def resume_after_confirm(self, messages: list[dict], pending_call: dict,
                                   token: str, approved: bool, exec_result: Any = None) -> AgentResult:
        """用户点确认/取消后续聊。exec_result 可由调用方传入（已执行的 createOrder 结果）。"""
        if approved:
            result = exec_result if exec_result is not None else await self._dispatch(
                token, "createOrder", pending_call["function"].get("arguments", "{}"))
        else:
            result = {"cancelled": True, "message": "用户取消了本次下单"}
        messages.append({"role": "tool", "tool_call_id": pending_call["id"],
                         "content": json.dumps(result, ensure_ascii=False)})
        return await self.step(messages, token)

    async def aclose(self) -> None:
        await self._http.aclose()
