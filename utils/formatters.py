"""Number and currency formatting helpers."""


def fmt_traffic(amount: int) -> str:
    """Format traffic amount with comma separators and unit."""
    if amount >= 100_000_000:
        return f"{amount / 100_000_000:.2f}亿流量"
    if amount >= 10_000:
        return f"{amount / 10_000:.2f}万流量"
    return f"{amount:,}流量"


def fmt_pct(value: float) -> str:
    """Format a 0-100 float as percentage string."""
    return f"{value:.2f}%"


def fmt_shares(shares: float) -> str:
    return f"{shares:.2f}%"


def fmt_reputation_buff(reputation: int) -> str:
    """Calculate and display the revenue buff from reputation.

    Buff = min(reputation * 0.1%, max_buff).  Non-stackable (single highest buff applies).
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
