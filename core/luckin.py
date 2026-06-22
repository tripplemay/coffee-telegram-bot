"""瑞幸开放平台端点常量与环境切换（逆向自官网 bundle，已联网核实）。"""
from __future__ import annotations

# ---- MCP gateway (订单工具 streamable-http 端点) ----
# 由前端 $n(): host 取决于环境前缀。
_MCP_HOSTS = {
    "prod": "gwmcp",
    "test03": "mcpgatewaytest03",
    "pre": "mcpgatewaypre",
}


def mcp_endpoint(env: str = "prod") -> str:
    host = _MCP_HOSTS.get(env, _MCP_HOSTS["prod"])
    return f"https://{host}.lkcoffee.com/order/user/mcp"


# ---- 登录站点 (Mini App 代理用) ----
LOGIN_ORIGIN = "https://open.lkcoffee.com"
LOGIN_CAPI = LOGIN_ORIGIN + "/capi"  # 前端请求助手统一前缀 /capi

# 登录链路端点 (相对 /capi)，均 POST + application/json，POST 需 ?_csrf=<csrfToken cookie>
EP_VALIDCODE = "/resource/m/sys/base/validcode"      # 发短信  {mobile, callCode, blackbox}
EP_SLIDER_VERIFY = "/resource/m/sys/base/sliderVerify"  # {sourceUrl, sliderType:0, blackbox, verifyParams, phone, countryNo}
EP_LOGIN_AI = "/resource/m/user/loginAi"             # {mobile, validateCode, countryNo, type:1}
EP_GET_TOKEN = "/resource/m/oauth/mcp/getToken"      # {oauthApp:"LUCKIN_MCP_AI"} -> content.luckyMcpToken
EP_DEL_TOKEN = "/resource/m/oauth/mcp/delToken"
EP_LOGOUT = "/resource/m/user/logout"

OAUTH_APP = "LUCKIN_MCP_AI"

# ---- 人机验证参数 ----
GEETEST_CAPTCHA_ID = "60d64df63d51f68279ed79e899a3f812"
GEETEST_CONFIG = {
    "captchaId": GEETEST_CAPTCHA_ID,
    "product": "bind",
    "language": "zho",
    "riskType": "slide",
    "hideSuccess": False,
}
TONGDUN_PARTNER = "luckincoffee"
TONGDUN_APP_NAME = "luckincoffee_xcx"
TONGDUN_SDK = "https://static.tongdun.net/captcha/main/tdc.js"
TONGDUN_FP_HOST = "https://fp.tongdun.net"

SLIDER_TYPE_GEE_TEST = 0  # Lo.GEE_TEST == 0 (整数，不是字符串)

# ---- 订单状态码 (queryOrderDetailInfo.orderStatus) ----
ORDER_STATUS = {
    10: "待付款",
    20: "下单成功",
    30: "制作中",
    60: "等待取餐",
    80: "已完成",
    100: "已取消",
}
