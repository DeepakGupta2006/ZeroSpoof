"""Runtime configuration, read from environment variables (see .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default)


def _bool(name: str, default: bool) -> bool:
    return _env(name, "1" if default else "0").lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    env: str = field(default_factory=lambda: _env("ZS_ENV", "dev"))

    # Storage. Empty REDIS_URL -> in-memory store. sqlite default -> zero-setup local dev.
    redis_url: str = field(default_factory=lambda: _env("REDIS_URL", ""))
    database_url: str = field(
        default_factory=lambda: _env("DATABASE_URL", f"sqlite:///{_ROOT / 'zerospoof.db'}")
    )

    # Secrets. CHANGE THESE IN PRODUCTION (the dev defaults are public).
    admin_key: str = field(default_factory=lambda: _env("ZS_ADMIN_KEY", "zs_admin_dev"))
    dev_api_key: str = field(default_factory=lambda: _env("ZS_DEV_API_KEY", "zs_live_dev"))
    token_secret: str = field(default_factory=lambda: _env("ZS_TOKEN_SECRET", "dev-token-secret-change-me"))
    token_ttl_s: int = field(default_factory=lambda: int(_env("ZS_TOKEN_TTL", "300")))

    # Where browsers should connect (Go gateway in the full stack, inference itself in lite mode).
    public_ws_url: str = field(default_factory=lambda: _env("ZS_PUBLIC_WS_URL", "ws://localhost:8000/ws"))

    # Rate limits
    api_rate_per_min: int = field(default_factory=lambda: int(_env("ZS_API_RATE_PER_MIN", "120")))
    ws_max_msgs_per_s: int = field(default_factory=lambda: int(_env("ZS_WS_MAX_MSGS_PER_S", "200")))
    ws_max_msg_bytes: int = field(default_factory=lambda: int(_env("ZS_WS_MAX_MSG_BYTES", str(512 * 1024))))
    require_nonce: bool = field(default_factory=lambda: _bool("ZS_REQUIRE_NONCE", False))

    # Verdict thresholds on the fused liveness probability.
    pass_threshold: float = field(default_factory=lambda: float(_env("ZS_PASS_T", "0.70")))
    fail_threshold: float = field(default_factory=lambda: float(_env("ZS_FAIL_T", "0.40")))
    min_seconds: float = field(default_factory=lambda: float(_env("ZS_MIN_SECONDS", "8")))
    # DEMO ONLY: when a webcam cannot show a pulse, ignore an inconclusive rPPG instead of capping the verdict at "review".
    # This weakens the face-swap defence. Refused in prod.
    demo_relax: bool = field(default_factory=lambda: _bool("ZS_DEMO_RELAX", False))

    # Optional model files (ONNX). Missing files -> that detector reports "unavailable"/falls back.
    models_dir: Path = field(default_factory=lambda: Path(_env("ZS_MODELS_DIR", str(_ROOT / "models"))))

    # Billing (ILLUSTRATIVE numbers from the project brief; validate with real prospects).
    base_price_usd: float = field(default_factory=lambda: float(_env("ZS_BASE_PRICE", "49")))
    included_checks: int = field(default_factory=lambda: int(_env("ZS_INCLUDED_CHECKS", "1000")))
    price_per_1000_usd: float = field(default_factory=lambda: float(_env("ZS_PRICE_PER_1000", "25")))

    cors_origins: list[str] = field(
        default_factory=lambda: [o for o in _env("ZS_CORS_ORIGINS", "http://localhost:3000").split(",") if o]
    )


settings = Settings()
