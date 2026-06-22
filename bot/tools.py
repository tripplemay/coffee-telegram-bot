"""瑞幸 8 个 MCP 工具 → LLM function-calling schema（OpenAI/aigc-gateway 格式）。

入参 schema 逆向自官网 bundle 的工具目录。出参不在此声明（由 MCP 返回，回灌给模型）。
"""
from __future__ import annotations

# createOrder 花真钱：永不交给 LLM 直接执行，必须经人工确认按钮（见 bot/flows.py）。
CONFIRM_REQUIRED = {"createOrder"}

# productList 单项（previewOrder / createOrder 共用）
_PRODUCT_ITEM = {
    "type": "object",
    "properties": {
        "amount": {"type": "integer", "description": "商品数量"},
        "productId": {"type": "integer", "description": "商品ID"},
        "skuCode": {"type": "string", "description": "商品 SKU 编码"},
    },
    "required": ["amount", "productId", "skuCode"],
}


def _fn(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


# geocodeAddress 不是瑞幸 MCP 工具，由 agent 本地用高德地理编码处理（见 bot/agent.py）
NON_MCP_TOOLS = {"geocodeAddress"}

TOOL_SCHEMAS: list[dict] = [
    _fn("geocodeAddress",
        "把地点名/地址/地标转成经纬度。当用户指定『在某地附近的店/某楼下/某路那家』时，先用它拿到坐标，再调 queryShopList。", {
            "address": {"type": "string", "description": "地点名称/地址/地标，如『成都港汇紫光星云中心』『天府三街』"},
        }, ["address"]),

    _fn("queryShopList", "瑞幸咖啡查询门店列表（按经纬度找附近门店）", {
        "deptName": {"type": "string", "description": "门店名称（可选，用于按名搜索）"},
        "longitude": {"type": "number", "description": "经度"},
        "latitude": {"type": "number", "description": "纬度"},
    }, ["longitude", "latitude"]),

    _fn("searchProductForMcp", "瑞幸咖啡根据用户 query 匹配商品推荐结果", {
        "deptId": {"type": "integer", "description": "门店ID"},
        "query": {"type": "string", "description": "用户原始查询文本"},
    }, ["deptId", "query"]),

    _fn("switchProduct", "瑞幸咖啡商品属性切换（如温度/杯型）", {
        "deptId": {"type": "integer", "description": "门店ID"},
        "productId": {"type": "integer", "description": "商品ID"},
        "skuCode": {"type": "string", "description": "商品 SKU 编码"},
        "attrOperationParam": {
            "type": "object",
            "description": "属性切换参数",
            "properties": {
                "attributeId": {"type": "integer", "description": "属性组ID"},
                "subAttr": {
                    "type": "object",
                    "description": "属性值操作信息",
                    "properties": {
                        "attributeId": {"type": "integer", "description": "属性值ID"},
                        "operation": {"type": "integer", "description": "操作类型，选中传 3"},
                    },
                    "required": ["attributeId", "operation"],
                },
            },
            "required": ["attributeId", "subAttr"],
        },
        "amount": {"type": "integer", "description": "商品数量"},
    }, ["deptId", "productId", "skuCode", "attrOperationParam", "amount"]),

    _fn("queryProductDetailInfo", "瑞幸咖啡查询商品详情", {
        "deptId": {"type": "integer", "description": "门店ID"},
        "productId": {"type": "integer", "description": "商品ID"},
    }, ["deptId", "productId"]),

    _fn("previewOrder", "瑞幸咖啡订单预览（下单前看价格/优惠/预计取餐时间）", {
        "deptId": {"type": "integer", "description": "门店ID"},
        "productList": {"type": "array", "description": "订单商品列表", "items": _PRODUCT_ITEM},
    }, ["deptId", "productList"]),

    _fn("createOrder", "瑞幸咖啡创建订单（⚠️ 花真钱，需用户确认后才可调用）", {
        "deptId": {"type": "integer", "description": "门店ID"},
        "productList": {"type": "array", "description": "订单商品列表", "items": _PRODUCT_ITEM},
        "longitude": {"type": "number", "description": "经度"},
        "latitude": {"type": "number", "description": "纬度"},
        "couponCodeList": {"type": "array", "items": {"type": "string"}, "description": "优惠券列表（可选）"},
    }, ["deptId", "productList", "longitude", "latitude"]),

    _fn("queryOrderDetailInfo", "瑞幸咖啡查询订单详情（状态/取餐码）", {
        "orderId": {"type": "string", "description": "订单ID"},
    }, ["orderId"]),

    _fn("cancelOrder", "瑞幸咖啡取消订单", {
        "orderId": {"type": "string", "description": "订单ID"},
    }, ["orderId"]),
]

TOOL_NAMES = {t["function"]["name"] for t in TOOL_SCHEMAS}
