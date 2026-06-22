# Phase 0 — 登录去风险 Spike

验证瑞幸登录能否在**我们自己的域名**上跑通，决定走 Mini App 还是降级「粘贴 Token」。

## 验证什么
- **Q1**：极验 v4 滑块（瑞幸 captchaId `60d64df63d51f68279ed79e899a3f812`）在我们的 cloudflared/ngrok 域名上**能否出题并解出**？
- **Q2**：服务端 httpx 串 `validcode → sliderVerify → loginAi → getToken` 能否拿到真实 `luckyMcpToken`？

## 运行
```bash
cd spike
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn spike_app:app --host 0.0.0.0 --port 8000
# 另开一个终端，出公网 HTTPS：
cloudflared tunnel --url http://localhost:8000     # 或 ngrok http 8000
```
拿到 `https://xxxx.trycloudflare.com` 后，**用手机**（Telegram WebView 或移动端浏览器）打开它，
依次：填手机号 → 点「获取验证码」（弹滑块）→ 收到短信填验证码 → 点「登录并获取 Token」。

控制台会打印每一步上游的状态码 / Set-Cookie / 响应体；页面底部「结果」面板显示同样的过程。

## 判定（看页面/控制台）
| 现象 | 结论 |
|---|---|
| 滑块出题、`onSuccess`、最终显示 `luckyMcpToken` | ✅ **GO** — 建完整 Mini App |
| `Geetest onError` / 滑块空白 / `gcaptcha4.geetest.com/load` 被拒 | ❌ **NO-GO** — 极验绑定了瑞幸域名 → 降级「粘贴 Token」 |
| 滑块通过但 `loginAi`/`getToken` 被风控拒 | ⚠️ 先试 `curl_cffi`（Chrome TLS 指纹）；仍拒 → 降级「粘贴 Token」 |

页面里有个 `<meta name="referrer" content="no-referrer">` 开关（默认注释），若滑块被拒可取消注释再试一次。

## 无副作用的连通性自检（不发短信、不登录）
`POST /api/getToken`（未登录）会让后端走完「GET 首页拿 CSRF/cookie → 调 `/capi/.../getToken`」，
返回一个「未登录」的 JSON。这能证明 `/capi` 前缀 + CSRF 双提交 + cookie jar 整条管道是通的，
且不会触发任何短信或下单。
