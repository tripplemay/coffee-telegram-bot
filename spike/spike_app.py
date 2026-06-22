"""
Phase 0 de-risk spike — Telegram Luckin (瑞幸) ordering bot.

GOAL — answer two empirical questions only:
  Q1. Does Luckin's Geetest v4 slider (captchaId 60d64df63d51f68279ed79e899a3f812)
      issue a solvable challenge when embedded on OUR OWN domain (cloudflared/ngrok)?
  Q2. Does the full server-side chain
        validcode -> sliderVerify -> loginAi -> getToken
      return a real `luckyMcpToken` when driven through our httpx cookie-jar proxy?

The browser page (index.html) runs Tongdun (blackbox) + Geetest (slider) on our
domain and posts the results to this backend. This backend keeps a per-browser
httpx session that:
  1. GETs the Luckin homepage to mint the `csrfToken` cookie + session cookies,
     and reads `window._csrf` (double-submit CSRF).
  2. POSTs each step to https://open.lkcoffee.com/capi/resource/m/...?_csrf=<token>
     as JSON, carrying those cookies, exactly like the real site.

RUN:
  pip install -r requirements.txt
  uvicorn spike_app:app --host 0.0.0.0 --port 8000
  cloudflared tunnel --url http://localhost:8000      # -> https://<random>.trycloudflare.com
  # open that HTTPS url ON YOUR PHONE (Telegram WebView / mobile Safari), then
  # enter phone -> solve slider -> enter SMS code.

WATCH the console: every upstream status / Set-Cookie / body is logged.

DECISION:
  * slider renders + onSuccess + non-empty luckyMcpToken  -> GO  (build full Mini App)
  * slider onError / blank / gcaptcha4 /load refused        -> NO-GO (domain-bound)
                                                              -> fall back to paste-token
  * slider OK but loginAi/getToken risk-rejected            -> try curl_cffi impersonation;
                                                              still rejected -> paste-token
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-5s  %(message)s")
log = logging.getLogger("spike")

LK_ORIGIN = "https://open.lkcoffee.com"
HOME = LK_ORIGIN + "/"
CAPI = LK_ORIGIN + "/capi"  # the site's request helper prefixes every call with /capi

# A realistic mobile-browser header set (server-side we can set Origin/Referer freely).
BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Origin": LK_ORIGIN,
    "Referer": LK_ORIGIN + "/",
}

app = FastAPI(title="luckin-login-spike")

INDEX_HTML = (Path(__file__).parent / "index.html").read_text(encoding="utf-8")

# One upstream session per browser (keyed by our own `sid` cookie). The httpx
# cookie jar carries csrfToken + LK_*_SSID across all four upstream calls.
SESSIONS: dict[str, dict] = {}


def _sid(request: Request) -> str:
    return request.cookies.get("sid") or "default"


async def _session(sid: str) -> dict:
    sess = SESSIONS.get(sid)
    if sess is None:
        client = httpx.AsyncClient(
            headers=BASE_HEADERS, http2=True, timeout=20.0, follow_redirects=True
        )
        sess = {"client": client, "csrf": None}
        SESSIONS[sid] = sess
    return sess


async def _ensure_csrf(sess: dict) -> None:
    """GET the homepage once to harvest the csrfToken cookie + session cookies."""
    if sess["csrf"]:
        return
    client: httpx.AsyncClient = sess["client"]
    r = await client.get(HOME, headers={"Accept": "text/html,application/xhtml+xml"})
    log.info("homepage GET -> %s; cookies now: %s", r.status_code, dict(client.cookies))
    # Double-submit CSRF: the `_csrf` query param must equal the csrfToken cookie.
    csrf = client.cookies.get("csrfToken")
    if not csrf:
        m = re.search(r"window\._csrf\s*=\s*'([^']+)'", r.text)
        csrf = m.group(1) if m else ""
    sess["csrf"] = csrf
    log.info("csrf token = %r", csrf)


async def _upstream(sess: dict, path: str, params: dict) -> dict:
    await _ensure_csrf(sess)
    client: httpx.AsyncClient = sess["client"]
    url = f"{CAPI}{path}?_csrf={sess['csrf']}"
    log.info(">>> POST %s  params=%s", path, {k: params[k] for k in params if k not in ("blackbox", "verifyParams")})
    r = await client.post(url, json=params)
    log.info("<<< %s  set-cookie=%s", r.status_code, r.headers.get("set-cookie"))
    log.info("    body: %s", r.text[:1000])
    try:
        return r.json()
    except Exception:
        return {"_status": r.status_code, "_raw": r.text}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    resp = HTMLResponse(INDEX_HTML)
    if not request.cookies.get("sid"):
        resp.set_cookie("sid", uuid.uuid4().hex, httponly=True, samesite="lax", secure=True)
    return resp


@app.post("/api/validcode")
async def validcode(request: Request):
    body = await request.json()
    sess = await _session(_sid(request))
    data = await _upstream(sess, "/resource/m/sys/base/validcode", {
        "mobile": str(body["mobile"]).strip(),
        "callCode": str(body.get("callCode", "86")),
        "blackbox": body.get("blackbox", ""),
    })
    return JSONResponse(data)


@app.post("/api/sliderVerify")
async def slider_verify(request: Request):
    body = await request.json()
    sess = await _session(_sid(request))
    data = await _upstream(sess, "/resource/m/sys/base/sliderVerify", {
        "sourceUrl": body.get("sourceUrl", "/resource/m/sys/base/validcode"),
        "sliderType": 0,  # Lo.GEE_TEST == 0  (NOT the string "GEE_TEST")
        "blackbox": body.get("blackbox", ""),
        "verifyParams": body["verifyParams"],  # JSON string of geetest getValidate()
        "phone": str(body["mobile"]).strip(),
        "countryNo": str(body.get("countryNo", "86")),
    })
    return JSONResponse(data)


@app.post("/api/loginAi")
async def login_ai(request: Request):
    body = await request.json()
    sess = await _session(_sid(request))
    data = await _upstream(sess, "/resource/m/user/loginAi", {
        "mobile": str(body["mobile"]).strip(),
        "validateCode": str(body["code"]).strip(),
        "countryNo": str(body.get("countryNo", "86")),
        "type": 1,
    })
    return JSONResponse(data)


@app.post("/api/getToken")
async def get_token(request: Request):
    sess = await _session(_sid(request))
    data = await _upstream(sess, "/resource/m/oauth/mcp/getToken", {"oauthApp": "LUCKIN_MCP_AI"})
    return JSONResponse(data)
