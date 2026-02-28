"""AI-assisted product R&D system.

Players submit a product proposal (text description). An AI model evaluates
the quality of the proposal and determines a permanent revenue boost (1-100%).
Players can also invest extra funds to hire more R&D staff (accelerate research).

Flow:
1. Player submits a product proposal text
2. AI evaluates the proposal and returns a score + feedback
3. Based on score + R&D investment, a permanent income boost is calculated
4. The product's daily_income is permanently increased

Requires ai_api_key to be configured. If not configured, falls back to a
keyword-based scoring system.
"""

from __future__ import annotations

import json
import logging
import re

from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from db.models import Product
from services.user_service import add_points, add_reputation

logger = logging.getLogger(__name__)

# Fallback keyword scoring when AI is not configured
POSITIVE_KEYWORDS = [
    "创新", "用户体验", "增长", "盈利", "市场", "竞争优势", "技术壁垒",
    "数据驱动", "可扩展", "生态", "平台", "差异化", "需求", "痛点",
    "商业模式", "变现", "复购", "留存", "转化", "智能", "自动化",
    "效率", "降本", "安全", "隐私", "合规", "社交", "网络效应",
]

R_AND_D_COST_PER_STAFF = 200  # cost per extra R&D staff member
MAX_EXTRA_RD_STAFF = 10


async def evaluate_proposal_ai(proposal: str) -> tuple[int, str]:
    """Use AI API to evaluate a product proposal.

    Returns (score 1-100, feedback text).
    """
    if not settings.ai_api_key:
        return _fallback_evaluate(proposal)

    try:
        import httpx
        prompt = (
            "你是一位资深产品经理和投资人。请评估以下产品方案，从以下维度打分：\n"
            "1. 创新性 (0-25分)\n"
            "2. 市场可行性 (0-25分)\n"
            "3. 技术可行性 (0-25分)\n"
            "4. 商业价值 (0-25分)\n\n"
            "产品方案:\n"
            f"{proposal}\n\n"
            "请以JSON格式返回：\n"
            '{"score": 总分(1-100), "feedback": "简短评价(50字以内)", '
            '"innovation": 创新分, "market": 市场分, "tech": 技术分, "business": 商业分}'
        )

        headers = {
            "Authorization": f"Bearer {settings.ai_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": settings.ai_model or "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7,
            "max_tokens": 300,
        }

        base_url = settings.ai_api_base_url or "https://api.openai.com/v1"
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{base_url}/chat/completions", json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]

            # Parse JSON from response
            json_match = re.search(r'\{[^}]+\}', content)
            if json_match:
                result = json.loads(json_match.group())
                score = max(1, min(100, int(result.get("score", 50))))
                feedback = result.get("feedback", "评估完成")
                return score, feedback
            else:
                return 50, "AI评估完成，但结果解析异常"

    except Exception as e:
        logger.warning("AI evaluation failed, using fallback: %s", e)
        return _fallback_evaluate(proposal)


def _fallback_evaluate(proposal: str) -> tuple[int, str]:
    """Keyword-based fallback scoring when AI is not available."""
    if len(proposal) < 20:
        return 15, "方案描述太简短，缺乏详细规划"

    score = 30  # base score
    matched = []
    for kw in POSITIVE_KEYWORDS:
        if kw in proposal:
            score += 3
            matched.append(kw)

    # Length bonus
    if len(proposal) > 100:
        score += 5
    if len(proposal) > 200:
        score += 5
    if len(proposal) > 500:
        score += 10

    score = max(1, min(100, score))
    if matched:
        feedback = f"方案涉及关键要素: {', '.join(matched[:5])}"
    else:
        feedback = "方案缺乏关键商业/技术要素"
    return score, feedback


async def apply_rd_result(
    session: AsyncSession,
    product_id: int,
    owner_user_id: int,
    score: int,
    extra_staff: int = 0,
) -> tuple[bool, str, int]:
    """Apply R&D result to a product.

    The permanent income boost = score * (1 + staff_bonus) / 100
    where staff_bonus = 0.05 per extra staff.
    Returns (success, message, income_increase).
    """
    product = await session.get(Product, product_id)
    if product is None:
        return False, "产品不存在", 0

    # Staff bonus: each extra staff adds 5% effectiveness
    staff_bonus = min(extra_staff, MAX_EXTRA_RD_STAFF) * 0.05
    boost_pct = max(0.01, (score / 100)) * (1 + staff_bonus)
    boost_pct = max(0.01, min(boost_pct, 1.0))  # clamp 1%-100%

    income_increase = max(1, int(product.daily_income * boost_pct))
    from services.product_service import MAX_PRODUCT_DAILY_INCOME
    income_increase = min(income_increase, MAX_PRODUCT_DAILY_INCOME - product.daily_income)
    product.daily_income += income_increase
    product.quality += max(1, score // 10)
    product.version += 1
    await session.flush()

    # Reputation and points for R&D
    rep = max(1, score // 5)
    await add_reputation(session, owner_user_id, rep)
    await add_points(owner_user_id, score // 2)

    return True, (
        f"研发完成! 评分: {score}/100\n"
        f"产品「{product.name}」永久收入+{income_increase} ({boost_pct*100:.1f}%)\n"
        f"新日收入: {product.daily_income}"
    ), income_increase
