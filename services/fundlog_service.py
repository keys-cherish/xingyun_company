"""资金流水日志系统。

记录所有资金变动（个人余额、公司积分），支持查询历史流水。

Redis keys:
  fundlog:user:{user_id}     — List of JSON log entries (个人账户流水)
  fundlog:company:{company_id} — List of JSON log entries (公司账户流水)
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Literal

from cache.redis_client import get_redis

# 保留最近N条日志
MAX_LOG_ENTRIES = 100
LOG_EXPIRE_DAYS = 30


async def log_fund_change(
    account_type: Literal["user", "company"],
    account_id: int,
    amount: int,
    reason: str,
    balance_after: int | None = None,
    extra: dict | None = None,
) -> None:
    """记录一笔资金变动。

    Args:
        account_type: "user" (个人) 或 "company" (公司)
        account_id: user.id 或 company.id
        amount: 变动金额（正=收入，负=支出）
        reason: 变动原因描述
        balance_after: 变动后余额（可选）
        extra: 额外信息（可选）
    """
    try:
        r = await get_redis()
    except Exception:
        # Redis 不可用时静默跳过日志（测试环境等）
        return

    key = f"fundlog:{account_type}:{account_id}"

    entry = {
        "ts": dt.datetime.now(dt.timezone.utc).isoformat(),
        "amount": amount,
        "reason": reason,
    }
    if balance_after is not None:
        entry["balance"] = balance_after
    if extra:
        entry.update(extra)

    try:
        # 左推入列表（最新的在前面）
        await r.lpush(key, json.dumps(entry, ensure_ascii=False))
        # 保留最近N条
        await r.ltrim(key, 0, MAX_LOG_ENTRIES - 1)
        # 设置过期时间
        await r.expire(key, LOG_EXPIRE_DAYS * 86400)
    except Exception:
        # 日志写入失败不影响主业务
        pass


async def get_fund_logs(
    account_type: Literal["user", "company"],
    account_id: int,
    limit: int = 20,
) -> list[dict]:
    """获取资金流水记录。

    Returns:
        List of log entries, newest first.
    """
    r = await get_redis()
    key = f"fundlog:{account_type}:{account_id}"

    raw_logs = await r.lrange(key, 0, limit - 1)
    logs = []
    for item in raw_logs:
        s = item if isinstance(item, str) else item.decode()
        try:
            logs.append(json.loads(s))
        except json.JSONDecodeError:
            continue
    return logs


def format_log_entry(entry: dict) -> str:
    """格式化单条日志。"""
    ts = entry.get("ts", "")
    # 解析ISO时间并转为本地时间显示
    try:
        dt_obj = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        time_str = dt_obj.strftime("%m/%d %H:%M")
    except (ValueError, AttributeError):
        time_str = ts[:16] if ts else "?"

    amount = entry.get("amount", 0)
    reason = entry.get("reason", "未知")
    balance = entry.get("balance")

    sign = "+" if amount >= 0 else ""
    balance_str = f" → {balance:,}" if balance is not None else ""

    return f"[{time_str}] {sign}{amount:,} | {reason}{balance_str}"
