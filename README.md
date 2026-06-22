# ☕ Telegram 瑞幸咖啡点单机器人

![ci](https://github.com/tripplemay/coffee-telegram-bot/actions/workflows/ci.yml/badge.svg)

用自然语言在 Telegram 里点瑞幸咖啡。LLM agent 编排瑞幸开放平台的 MCP 工具，
完成「找店 → 选品 → 预览 → 确认 → 下单 → 支付 → 取餐」全流程。

- **交互**：纯 LLM Agent（aigc-gateway，OpenAI 兼容，默认 `deepseek-v3`）
- **登录**：① Telegram Mini App 自助登录（手机号+滑块+短信，需先跑通 P0）；② 粘贴 Token 兜底
- **安全**：`createOrder` 花真钱，**必须用户点「✅确认」后才执行** + 单日消费上限 + token 加密存储

## 架构
```
Telegram 用户 ─► bot/ (python-telegram-bot, 长轮询)
                 ├─ agent.py   LLM function-calling 循环（createOrder 拦截 → 人工确认）
                 ├─ mcp_client 按用户 token 连 gwmcp 端点（8 个工具）
                 └─ flows/ui   预览/确认/支付二维码/状态轮询
web/ (FastAPI)  ─► Mini App 登录页 + 登录代理（cookie jar 串瑞幸登录 4 接口）  ← P1，gated on P0
core/           ─► config / luckin 端点 / db(SQLite+Fernet 加密 token)
spike/          ─► P0 去风险：验证极验滑块能否在自有域名出题
```

## 快速开始（粘贴 Token 路径，立即可用）
```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env        # 填 BOT_TOKEN；FERNET_KEY/AIGC_API_KEY 已在本地 .env 配好
python -m bot.main          # 长轮询启动
```
然后在 Telegram 里：
1. `/start`
2. 在 https://open.lkcoffee.com 登录并复制你的 Token → `/login <token>`
3. 点「📍 发送我的位置」
4. 直接说「来杯热的生椰拿铁」→ 看预览 → 点「✅ 确认支付」→ 扫码付款 → 收取餐码

## Mini App 自助登录（P1，先跑 P0）
见 [`spike/README.md`](spike/README.md)：跑通极验滑块在自有域名的验证后，再启用 `web/` 的 Mini App 登录按钮
（在 `.env` 配 `PUBLIC_BASE_URL` 为 cloudflared/ngrok 域名）。

## 部署（自有 VPS + CI/CD）
长轮询 bot 无需公网域名，只要 24/7 常驻进程。`.github/workflows/` 提供：
- **ci**：每次 push/PR 跑 pytest。
- **deploy**：push 到 `main` 自动 SSH 部署到 VPS 并 `systemctl restart`（未配置 `VPS_*` secrets 时自动跳过）。

一次性配置见 [`deploy/SETUP.md`](deploy/SETUP.md)（VPS 建 venv + `.env` + systemd 服务，GitHub 配 `VPS_HOST/USER/SSH_KEY/PATH` secrets）。

## 测试
```bash
pytest            # 含 createOrder 护栏测试（确认前绝不下单）
```

## 配置（.env）
| 键 | 说明 |
|---|---|
| `BOT_TOKEN` | BotFather `/newbot` 获取 |
| `AIGC_API_KEY` | aigc-gateway `pk_` key（已配） |
| `LLM_MODEL` | 默认 `deepseek-v3`，可切 `qwen3.5-plus`/`kimi-k2.5`/`claude-opus-4.7` |
| `FERNET_KEY` | token 加密密钥（已生成） |
| `PUBLIC_BASE_URL` | Mini App 用的公网 HTTPS 域名 |
| `LUCKIN_ENV` | `prod`/`test03`/`pre` |
| `DAILY_SPEND_LIMIT` | 单日消费上限（元） |
