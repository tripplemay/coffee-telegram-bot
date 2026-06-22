"""集中配置：从 .env 读取，schema 校验。"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str = ""
    public_base_url: str = ""
    llm_model: str = "deepseek-v3"
    aigc_base_url: str = "https://aigc.guangai.ai/v1"
    aigc_api_key: str = ""
    luckin_env: str = "prod"  # prod | test03 | pre
    fernet_key: str = ""
    db_path: str = "coffee.db"
    daily_spend_limit: float = 100.0
    bridge_secret: str = ""  # 渠道服务 /message 的共享密钥（微信桥接用），留空则不校验
    amap_key: str = ""       # 高德 Web 服务 key，用于「地址→GCJ-02 坐标」地理编码
    wechat_push_url: str = ""  # 微信 bridge 入站推送端点基址（如 http://127.0.0.1:8300），用于登录/定位成功回推
    # ---- 语音转写（云 ASR；网关无 ASR 模态，必须外接）----
    asr_provider: str = ""    # "" 关闭 | dashscope(阿里) | tencent(腾讯) | iflytek(讯飞)
    asr_api_key: str = ""     # 单 key 厂商（阿里 DashScope）
    asr_app_id: str = ""      # 讯飞 APPID / 腾讯 SecretId
    asr_api_secret: str = ""  # 讯飞 APISecret / 腾讯 SecretKey


@lru_cache
def get_settings() -> Settings:
    return Settings()


def login_base_url() -> str:
    """登录页公网 URL：优先读 cloudflared 写入的 web/.public_url，回退 .env 的 PUBLIC_BASE_URL。"""
    for p in ("web/.public_url", "/opt/coffee-bot/web/.public_url"):
        try:
            u = open(p, encoding="utf-8").read().strip()
            if u:
                return u.rstrip("/")
        except OSError:
            continue
    return get_settings().public_base_url.rstrip("/")
