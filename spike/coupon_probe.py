"""消费版 H5 (m.lkcoffee.com) 优惠券探针 — 逆向自前端 bundle，分步执行（会话持久化到 /tmp）。

为什么分步：发短信 → 等用户读验证码 → 登录，跨多次调用，必须把 cookie/csrf 会话存盘续用。

用法（依次）：
  python spike/coupon_probe.py home                 # 起会话：GET 首页，抓 csrf + cookie
  python spike/coupon_probe.py sendcode <手机号>     # 发短信（验证无滑块）。⚠️ 会给该号码真发短信
  python spike/coupon_probe.py login <手机号> <验证码> # 登录，拿消费版 session
  python spike/coupon_probe.py enter <deptId>        # 只读：某门店可领券列表 + myCouponList
  python spike/coupon_probe.py detail <deptId> <sendProposalNo>  # 只读：单券详情（面额/门槛）
  python spike/coupon_probe.py claim <deptId> <sendProposalNo>   # ⚠️ 写操作：真领券，仅手动显式调用

端点（bundle 实证）：
  csrf = cookie LK_prod_csrfToken；所有 POST 走 /capi 前缀 + ?_csrf= + 头 x-csrf-token；X-LK-MID 登录后才有、可空。
  /resource/m/sys/base/validcode {mobile,countryNo} · /resource/m/user/login {type:1,mobile,countryNo,validateCode}
  /resource/market/receiveCoupon/{enter|receiveCouponDetail|receiveCouponSend}
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

STATE = Path("/tmp/lk_coupon_probe.json")
ORIGIN = "https://m.lkcoffee.com"
CAPI = ORIGIN + "/capi"
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1")


def base_headers() -> dict:
    return {"User-Agent": UA, "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9", "Origin": ORIGIN, "Referer": ORIGIN + "/"}


def load() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"cookies": {}, "csrf": "", "member_id": ""}


def save(s: dict) -> None:
    STATE.write_text(json.dumps(s, ensure_ascii=False))


def make_client(s: dict) -> httpx.Client:
    c = httpx.Client(headers=base_headers(), timeout=20, follow_redirects=True)
    for k, v in s.get("cookies", {}).items():
        c.cookies.set(k, v, domain="m.lkcoffee.com", path="/")
    return c


def sync(c: httpx.Client, s: dict) -> None:
    for k, v in c.cookies.items():
        s["cookies"][k] = v


def post(c: httpx.Client, s: dict, path: str, body: dict):
    csrf = s.get("csrf", "")
    headers = {"x-csrf-token": csrf, "X-LK-MID": s.get("member_id", ""),
               "Content-Type": "application/json"}
    r = c.post(f"{CAPI}{path}?_csrf={csrf}", json=body, headers=headers)
    sync(c, s)
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, r.text[:600]


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd = sys.argv[1]
    s = load()
    c = make_client(s)
    try:
        if cmd == "home":
            r = c.get(ORIGIN + "/", headers={"Accept": "text/html,*/*"})
            sync(c, s)
            s["csrf"] = c.cookies.get("LK_prod_csrfToken") or s.get("csrf", "")
            save(s)
            print("status", r.status_code, "| csrf:", (s["csrf"] or "")[:12],
                  "| cookies:", list(s["cookies"]))
        elif cmd == "sendcode":
            st, j = post(c, s, "/resource/m/sys/base/validcode",
                         {"mobile": sys.argv[2], "countryNo": "86"})
            save(s)
            print("HTTP", st, json.dumps(j, ensure_ascii=False)[:700])
        elif cmd == "login":
            st, j = post(c, s, "/resource/m/user/login",
                         {"type": 1, "mobile": sys.argv[2], "countryNo": "86",
                          "validateCode": sys.argv[3]})
            content = j.get("content") if isinstance(j, dict) else None
            if isinstance(content, dict):
                mid = content.get("memberId") or content.get("userId") or content.get("member_id")
                if mid:
                    s["member_id"] = str(mid)
            save(s)
            print("HTTP", st, "| member_id:", s.get("member_id", ""),
                  json.dumps(j, ensure_ascii=False)[:900])
        elif cmd == "enter":
            st, j = post(c, s, "/resource/market/receiveCoupon/enter",
                         {"deptId": int(sys.argv[2]), "type": 4})
            save(s)
            print("HTTP", st, json.dumps(j, ensure_ascii=False)[:2500])
        elif cmd == "detail":
            st, j = post(c, s, "/resource/market/receiveCoupon/receiveCouponDetail",
                         {"sendProposalNo": sys.argv[3], "deptId": int(sys.argv[2])})
            save(s)
            print("HTTP", st, json.dumps(j, ensure_ascii=False)[:2500])
        elif cmd == "claim":  # ⚠️ 真实写操作（领券）——仅在显式确认后手动调用
            st, j = post(c, s, "/resource/market/receiveCoupon/receiveCouponSend",
                         {"type": 1, "sendProposalNo": sys.argv[3], "deptId": int(sys.argv[2])})
            save(s)
            print("HTTP", st, json.dumps(j, ensure_ascii=False)[:1500])
        else:
            print("unknown cmd:", cmd)
            print(__doc__)
    finally:
        c.close()


if __name__ == "__main__":
    main()
