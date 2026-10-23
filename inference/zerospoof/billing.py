"""Usage metering + illustrative pricing: base tier + usage billed per 1,000 checks.

All prices are configuration (ZS_BASE_PRICE, ZS_INCLUDED_CHECKS, ZS_PRICE_PER_1000). They are
the brief's illustrative starting hypothesis, not validated prices.
"""
from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from typing import Any

from .config import settings


def period_start(now: float | None = None) -> float:
    d = datetime.fromtimestamp(now or time.time(), tz=timezone.utc)
    return datetime(d.year, d.month, 1, tzinfo=timezone.utc).timestamp()


def summary(db, key_id: str | None) -> dict[str, Any]:
    start = period_start()
    by_verdict = db.usage_since(key_id, start)
    checks = sum(by_verdict.values())
    extra = max(0, checks - settings.included_checks)
    overage = math.ceil(extra / 1000) * settings.price_per_1000_usd if extra else 0.0
    return {
        "period_start": start,
        "checks": checks,
        "by_verdict": by_verdict,
        "included_checks": settings.included_checks,
        "overage_checks": extra,
        "base_price_usd": settings.base_price_usd,
        "price_per_1000_usd": settings.price_per_1000_usd,
        "estimated_total_usd": round(settings.base_price_usd + overage, 2),
        "note": "Illustrative pricing; configure via ZS_BASE_PRICE / ZS_INCLUDED_CHECKS / ZS_PRICE_PER_1000.",
    }
