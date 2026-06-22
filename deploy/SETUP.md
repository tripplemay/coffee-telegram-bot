# 部署到自有 VPS（systemd + GitHub Actions 自动部署）

长轮询 bot **不需要公网域名/端口**，只要一台 24/7 常驻的 Linux 机器。

## 一、VPS 一次性配置

需要 Python 3.10+（建议 3.11）。

```bash
# 1. 拉代码到固定目录
sudo mkdir -p /opt/coffee-bot && sudo chown "$USER" /opt/coffee-bot
git clone https://github.com/<你的账号>/<仓库名> /opt/coffee-bot
cd /opt/coffee-bot

# 2. 建虚拟环境并安装
python3.11 -m venv .venv
.venv/bin/pip install -e .

# 3. 配置运行时密钥（绝不入库）
cp .env.example .env
#   填入真实 BOT_TOKEN、AIGC_API_KEY；生成 FERNET_KEY：
.venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
#   把上面输出填进 .env 的 FERNET_KEY=

# 4. 安装 systemd 服务（按需改 service 里的 User / 路径）
sudo cp deploy/coffee-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now coffee-bot
sudo systemctl status coffee-bot   # 应为 active (running)
```

让部署用户能免密重启服务（供 CI 自动部署）：

```bash
echo "$USER ALL=(ALL) NOPASSWD: /bin/systemctl restart coffee-bot" | sudo tee /etc/sudoers.d/coffee-bot
```

## 二、GitHub Actions 自动部署

1. 生成一对部署专用 SSH 密钥（本地）：
   ```bash
   ssh-keygen -t ed25519 -f coffee_deploy -N "" -C "gh-actions-deploy"
   ```
   - 公钥 `coffee_deploy.pub` → 追加到 VPS 部署用户的 `~/.ssh/authorized_keys`
   - 私钥 `coffee_deploy` 的**全文**→ 下面的 `VPS_SSH_KEY`

2. 仓库 → Settings → Secrets and variables → Actions → New repository secret，添加：

   | Secret | 值 |
   |---|---|
   | `VPS_HOST` | VPS IP 或域名 |
   | `VPS_USER` | 部署用户名 |
   | `VPS_SSH_KEY` | 部署私钥全文（含 BEGIN/END 行） |
   | `VPS_PATH` | `/opt/coffee-bot` |
   | `VPS_PORT` | SSH 端口（默认 22，可不填） |

配好后，每次 push 到 `main`，`deploy.yml` 会自动 SSH 进 VPS：`git reset --hard origin/main` → 安装依赖 → `systemctl restart coffee-bot`。未配置 secrets 时该工作流自动跳过、不报错。

## 三、运维

```bash
journalctl -u coffee-bot -f          # 看日志
sudo systemctl restart coffee-bot    # 手动重启
bash deploy/deploy.sh                # 手动拉最新并重启
```

注意：`coffee.db`（加密 token 库）存在 VPS 本地的 WorkingDirectory，不入库；换机/重装需用户重新 `/login`。
