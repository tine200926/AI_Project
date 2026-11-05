"""執行設定：全部從環境變數讀取（也會讀專案根目錄的 .env）。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()


@dataclass
class Settings:
    data_mode: str = os.getenv("TWSA_DATA_MODE", "live").lower()
    finmind_token: str = os.getenv("FINMIND_TOKEN", "")
    finlab_token: str = os.getenv("FINLAB_API_TOKEN", "")
    anthropic_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    ai_model: str = os.getenv("TWSA_AI_MODEL", "claude-opus-5-5")
    cache_dir: Path = ROOT / os.getenv("TWSA_CACHE_DIR", ".cache")
    cache_ttl: int = int(os.getenv("TWSA_CACHE_TTL", "21600"))
    http_timeout: int = int(os.getenv("TWSA_HTTP_TIMEOUT", "20"))

    @property
    def demo(self) -> bool:
        return self.data_mode == "demo"

    @property
    def ai_enabled(self) -> bool:
        return bool(self.anthropic_key) or bool(os.getenv("ANTHROPIC_AUTH_TOKEN"))


settings = Settings()
