"""Number, currency, and quota formatting helpers."""

from __future__ import annotations

CURRENCY_NAME = "金币"


def fmt_currency(amount: int) -> str:
    """Format in-game currency amount."""
    return f"{amount:,} {CURRENCY_NAME}"


def fmt_quota(amount_mb: int) -> str:
    """Format quota amount with MB/GB/TB units (1024 based)."""
    if amount_mb >= 1024 * 1024:  # >= 1TB
        return f"{amount_mb / (1024 * 1024):.2f}TB ({amount_mb:,}MB)"
    if amount_mb >= 1024:  # >= 1GB
        return f"{amount_mb / 1024:.2f}GB ({amount_mb:,}MB)"
    return f"{amount_mb:,}MB"


def fmt_traffic(amount: int) -> str:
    """Backward-compatible alias: legacy 'traffic' now represents currency."""
    return fmt_currency(amount)


def fmt_pct(value: float) -> str:
    """Format a 0-100 float as percentage string."""
    return f"{value:.2f}%"


def fmt_shares(shares: float) -> str:
    return f"{shares:.2f}%"


def fmt_reputation_buff(reputation: int) -> str:
    """Calculate and display revenue buff from reputation.

    Buff = min(reputation * 0.1%, max_buff). Non-stackable.
    """
    from config import settings

    buff = min(reputation * 0.001, settings.max_reputation_buff_pct)
    return f"+{buff * 100:.1f}%营收"


def reputation_buff_multiplier(reputation: int) -> float:
    """Return the multiplier (e.g. 1.15 for +15%)."""
    from config import settings

    buff = min(reputation * 0.001, settings.max_reputation_buff_pct)
    return 1.0 + buff


def compact_number(n: int) -> str:
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)
