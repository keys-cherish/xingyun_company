"""AI-assisted product R&D system with practical copywriting critique.

Features:
1. Proposal evaluation (AI when configured, balanced fallback otherwise)
2. Permanent product income boost
3. Optional extra R&D staff investment
4. Keyword trigger for "春日影" themed special effects
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from db.models import Product
from services.user_service import add_points, add_reputation

logger = logging.getLogger(__name__)

R_AND_D_COST_PER_STAFF = 200
MAX_EXTRA_RD_STAFF = 10
MAX_RD_BOOST_PCT = 1.20  # Allow themed trigger to exceed normal 100% cap slightly.

HARUHIKAGE_KEYWORDS = ("春日影", "haruhikage", "mygo")
HARUHIKAGE_THEME_LINES = [
    "名场面触发：「为什么要演奏春日影？」舆论热度与讨论量同步抬升。",
    "在迷惘与并肩之间制造转折，让方案具备情绪拉力。",
    "先写清冲突，再给解法，用阶段性胜利来承接情感爆发。",
    "聚焦“低谷 -> 反打 -> 兑现”三段叙事，避免空洞口号。",
]
HARUHIKAGE_EMOJIS = ("🎸", "🌸", "🎭", "🔥", "⚡", "💥")
SOUL_QUESTION_TEMPLATES = (
    "为什么要{topic}？",
    "为什么要把{topic}做到极致？",
    "为什么要在这个节点做{topic}？",
)
SOUL_TOPIC_KEYWORDS = (
    "降本增效",
    "用户增长",
    "留存",
    "转化",
    "商业化",
    "口碑",
    "效率",
    "合规",
    "体验",
    "产品力",
)
HARUHIKAGE_MEME_LINES = (
    "🎸 舞台亮起，先问一句：为什么要{topic}？",
    "🌸 情绪拉满不是终点，落地才是答案。",
    "🔥 先打磨硬实力，再追求高光时刻。",
    "⚡ 方案不是宣言，要拿指标说话。",
)

HYPE_WORDS = ("颠覆", "革命性", "躺赚", "稳赚", "秒杀", "无敌", "爆款", "全网第一")
DEFAULT_AI_BASE_URL = "https://api.openai.com/v1"


def _default_system_prompt() -> str:
    return (
        "你是“产品方案评审官”，给出专业、可执行、不过度苛刻的评分。"
        "在指出问题的同时，优先提供可落地改进建议。"
    )


def _contains_any(text: str, words: tuple[str, ...] | list[str]) -> bool:
    return any(w in text for w in words)


def _count_hits(text: str, words: tuple[str, ...] | list[str]) -> int:
    return sum(1 for w in words if w in text)


def _contains_haruhikage_keyword(proposal: str) -> bool:
    lower = proposal.lower()
    return any(k in proposal or k in lower for k in HARUHIKAGE_KEYWORDS)


def _pick_by_seed(items: tuple[str, ...] | list[str], seed: int) -> str:
    if not items:
        return ""
    return items[seed % len(items)]


def _extract_soul_topic(proposal: str) -> str:
    for kw in SOUL_TOPIC_KEYWORDS:
        if kw in proposal:
            return kw
    for generic in ("市场", "产品", "研发", "创新", "效率"):
        if generic in proposal:
            return generic
    return "春日影"


def _build_haruhikage_effect(proposal: str, score: int) -> dict[str, Any] | None:
    if not _contains_haruhikage_keyword(proposal):
        return None

    topic = _extract_soul_topic(proposal)
    soul_question = _pick_by_seed(SOUL_QUESTION_TEMPLATES, score).format(topic=topic)
    meme_lines = [line.format(topic=topic) for line in HARUHIKAGE_MEME_LINES]
    emoji_pack = "".join(_pick_by_seed(HARUHIKAGE_EMOJIS, score + i) for i in range(3))

    if score >= 85:
        return {
            "name": "春日影·终幕共鸣",
            "income_multiplier": 1.18,
            "reputation_bonus": 8,
            "quality_bonus": 3,
            "flavor_text": HARUHIKAGE_THEME_LINES[score % len(HARUHIKAGE_THEME_LINES)],
            "soul_question": soul_question,
            "emoji_pack": emoji_pack,
            "meme_lines": meme_lines,
        }
    if score >= 70:
        return {
            "name": "春日影·舞台回响",
            "income_multiplier": 1.12,
            "reputation_bonus": 5,
            "quality_bonus": 2,
            "flavor_text": HARUHIKAGE_THEME_LINES[score % len(HARUHIKAGE_THEME_LINES)],
            "soul_question": soul_question,
            "emoji_pack": emoji_pack,
            "meme_lines": meme_lines,
        }
    return {
        "name": "春日影·余光残响",
        "income_multiplier": 1.06,
        "reputation_bonus": 2,
        "quality_bonus": 1,
        "flavor_text": HARUHIKAGE_THEME_LINES[score % len(HARUHIKAGE_THEME_LINES)],
        "soul_question": soul_question,
        "emoji_pack": emoji_pack,
        "meme_lines": meme_lines,
    }


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _format_strict_feedback(
    *,
    score: int,
    innovation: int,
    market: int,
    tech: int,
    business: int,
    verdict: str,
    critique: list[str],
    suggestions: list[str],
) -> str:
    flaws = "；".join(critique[:4]) if critique else "未给出明确缺陷"
    tips = "；".join(suggestions[:3]) if suggestions else "请补充用户场景、指标与商业闭环。"
    return (
        "【AI方案评审】\n"
        f"结论: {verdict}\n"
        f"创新/市场/技术/商业: {innovation}/{market}/{tech}/{business}\n"
        f"主要缺陷: {flaws}\n"
        f"改进建议: {tips}\n"
        f"综合得分: {score}/100"
    )


def _strict_fallback_evaluate(proposal: str) -> tuple[int, str]:
    text = proposal.strip()

    if len(text) < 10:
        return 20, "【AI方案评审】文本过短，信息不足。请补充用户、场景、指标与盈利路径。"

    # 5 dimensions * 20 = 100
    problem = 0
    scenario = 0
    business = 0
    tech = 0
    validation = 0
    flaws: list[str] = []
    suggestions: list[str] = []

    problem_words = ("痛点", "问题", "难点", "成本", "低效", "流失", "客诉")
    user_words = ("用户", "客群", "画像", "受众", "中小企业", "管理者", "工厂")
    if _contains_any(text, problem_words):
        problem += 8
    else:
        flaws.append("未清楚定义核心痛点")
        suggestions.append("先写明“谁在什么场景下遇到什么问题”")
    if _contains_any(text, user_words):
        problem += 6
    else:
        flaws.append("目标用户画像模糊")
    if len(text) >= 120:
        problem += 6
    elif len(text) >= 60:
        problem += 3
    else:
        flaws.append("背景描述过短，信息密度不足")

    scenario_words = ("场景", "流程", "模块", "功能", "步骤", "接口", "交付")
    diff_words = ("差异化", "壁垒", "竞品", "替代", "优势")
    if _contains_any(text, scenario_words):
        scenario += 8
    else:
        flaws.append("缺少具体功能或流程拆解")
        suggestions.append("按“输入-处理-输出”写核心流程")
    if _contains_any(text, diff_words):
        scenario += 6
    else:
        flaws.append("未说明相对现有方案的差异优势")
    if re.search(r"\d+[%天月人项倍]", text):
        scenario += 6
    else:
        flaws.append("缺少可量化目标（如转化率、周期、成本）")

    business_words = ("盈利", "变现", "订阅", "付费", "客单价", "利润", "ROI", "回本")
    growth_words = ("获客", "留存", "复购", "转化", "渠道", "销售", "投放")
    if _contains_any(text, business_words):
        business += 10
    else:
        flaws.append("商业闭环不完整")
        suggestions.append("补充定价、回本周期、获客与续费策略")
    if _contains_any(text, growth_words):
        business += 6
    else:
        flaws.append("增长路径不清晰")
    if re.search(r"(成本|毛利|净利|预算|现金流)", text):
        business += 4

    tech_words = ("架构", "技术", "算法", "稳定性", "扩展", "并发", "性能")
    compliance_words = ("隐私", "安全", "合规", "风控", "权限", "审计")
    if _contains_any(text, tech_words):
        tech += 10
    else:
        flaws.append("技术实现路径不清晰")
    if _contains_any(text, compliance_words):
        tech += 6
    else:
        flaws.append("合规与风险控制考虑不足")
        suggestions.append("补充权限边界、隐私保护与失败回滚机制")
    if re.search(r"(里程碑|MVP|迭代|灰度|上线)", text, re.IGNORECASE):
        tech += 4

    metric_words = ("KPI", "转化率", "留存率", "ARPU", "NPS", "LTV", "CAC", "DAU", "MAU")
    experiment_words = ("A/B", "AB测试", "试点", "访谈", "样本", "问卷", "埋点")
    if _contains_any(text, metric_words):
        validation += 10
    else:
        flaws.append("缺少成效衡量指标")
        suggestions.append("给出至少3个验收指标和基线值")
    if _contains_any(text, experiment_words):
        validation += 6
    else:
        flaws.append("缺少验证方案（试点/AB/访谈）")
    if re.search(r"\d+", text):
        validation += 4

    score = problem + scenario + business + tech + validation
    # Mild leniency: avoid overly harsh baseline for normal proposals.
    score += 8

    hype_hits = _count_hits(text, HYPE_WORDS)
    if hype_hits > 0:
        score -= min(8, hype_hits * 2)
        flaws.append("营销口号偏多，实证信息偏少")

    score = max(20, min(100, score))
    verdict = "可推进" if score >= 62 else "需重做"
    feedback = _format_strict_feedback(
        score=score,
        innovation=min(25, int(problem * 0.7 + scenario * 0.3)),
        market=min(25, int(problem * 0.4 + business * 0.6)),
        tech=min(25, int(tech * 1.25)),
        business=min(25, int(business * 1.25)),
        verdict=verdict,
        critique=flaws,
        suggestions=suggestions,
    )
    return score, feedback


def _normalize_completion_url(base_url: str) -> str:
    candidate = (base_url or "").strip() or DEFAULT_AI_BASE_URL
    candidate = candidate.rstrip("/")
    if candidate.endswith("/chat/completions"):
        return candidate
    return f"{candidate}/chat/completions"


def _parse_extra_headers(raw_headers_json: str) -> dict[str, str]:
    raw = (raw_headers_json or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            return {}
        return {str(k): str(v) for k, v in parsed.items()}
    except Exception:
        logger.warning("Invalid AI_EXTRA_HEADERS_JSON, ignored")
        return {}


def _extract_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_chunks: list[str] = []
        for item in content:
            if isinstance(item, dict):
                txt = item.get("text")
                if isinstance(txt, str):
                    text_chunks.append(txt)
        return "\n".join(text_chunks)
    return str(content)


def _parse_sse_to_json(raw_text: str) -> dict[str, Any]:
    for line in raw_text.splitlines():
        ln = line.strip()
        if not ln.startswith("data:"):
            continue
        payload = ln[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            parsed = json.loads(payload)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            continue
    return {}


async def evaluate_proposal_ai(proposal: str) -> tuple[int, str, dict[str, Any] | None]:
    """Evaluate a proposal and return (score, feedback, special_effect)."""
    if not settings.ai_enabled or not settings.ai_api_key.strip():
        score, feedback = _strict_fallback_evaluate(proposal)
        return score, feedback, _build_haruhikage_effect(proposal, score)

    try:
        import httpx

        user_prompt = (
            "请对下面方案做专业评审，按以下维度输出:\n"
            "1) 创新性(0-25)\n"
            "2) 市场可行性(0-25)\n"
            "3) 技术可行性(0-25)\n"
            "4) 商业价值(0-25)\n"
            "并额外给出 verdict(可推进/需重做)、critique(3-5条硬伤)、suggestions(3条可执行改进)。\n"
            "要求：基于证据评分，不过度苛刻；指出风险的同时给出可执行改进。\n\n"
            f"方案文本:\n{proposal}\n\n"
            "请只返回 JSON:\n"
            '{"score": 1-100, "innovation": 0-25, "market": 0-25, "tech": 0-25, '
            '"business": 0-25, "verdict": "可推进或需重做", '
            '"critique": ["..."], "suggestions": ["..."]}'
        )

        system_prompt = (settings.ai_system_prompt or "").strip() or _default_system_prompt()
        headers = {
            "Authorization": f"Bearer {settings.ai_api_key}",
            "Content-Type": "application/json",
        }
        headers.update(_parse_extra_headers(settings.ai_extra_headers_json))

        temperature = max(0.0, min(2.0, float(settings.ai_temperature)))
        top_p = max(0.0, min(1.0, float(settings.ai_top_p)))
        max_tokens = max(128, int(settings.ai_max_tokens))
        payload = {
            "model": (settings.ai_model or "").strip() or "gpt-4o-mini",
            "stream": False,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "top_p": top_p,
            "max_tokens": max_tokens,
        }

        completion_url = _normalize_completion_url(settings.ai_api_base_url)
        timeout = max(5, int(settings.ai_timeout_seconds))
        retry_times = max(0, int(settings.ai_max_retries))
        retry_backoff = max(0.2, float(settings.ai_retry_backoff_seconds))

        data: dict[str, Any] = {}
        for attempt in range(retry_times + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(completion_url, json=payload, headers=headers)
                    resp.raise_for_status()
                    try:
                        data = resp.json()
                    except Exception:
                        data = _parse_sse_to_json(resp.text)
                break
            except Exception:
                if attempt >= retry_times:
                    raise
                await asyncio.sleep(retry_backoff * (attempt + 1))

        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = _extract_content_text(message.get("content", ""))

        json_match = re.search(r"\{.*\}", content, re.S)
        if not json_match:
            score, feedback = _strict_fallback_evaluate(proposal)
            return score, feedback, _build_haruhikage_effect(proposal, score)

        result = json.loads(json_match.group())
        innovation = max(0, min(25, _safe_int(result.get("innovation"), 0)))
        market = max(0, min(25, _safe_int(result.get("market"), 0)))
        tech = max(0, min(25, _safe_int(result.get("tech"), 0)))
        business = max(0, min(25, _safe_int(result.get("business"), 0)))
        raw_score = _safe_int(result.get("score"), innovation + market + tech + business)
        score = max(1, min(100, raw_score))

        critique = result.get("critique", [])
        suggestions = result.get("suggestions", [])
        if not isinstance(critique, list):
            critique = [str(critique)]
        if not isinstance(suggestions, list):
            suggestions = [str(suggestions)]

        verdict = str(result.get("verdict", "需重做")).strip() or "需重做"
        feedback = _format_strict_feedback(
            score=score,
            innovation=innovation,
            market=market,
            tech=tech,
            business=business,
            verdict=verdict,
            critique=[str(x) for x in critique],
            suggestions=[str(x) for x in suggestions],
        )
        return score, feedback, _build_haruhikage_effect(proposal, score)

    except Exception as e:
        logger.warning("AI evaluation failed, using strict fallback: %s", e)
        score, feedback = _strict_fallback_evaluate(proposal)
        return score, feedback, _build_haruhikage_effect(proposal, score)


async def apply_rd_result(
    session: AsyncSession,
    product_id: int,
    owner_user_id: int,
    score: int,
    extra_staff: int = 0,
    special_effect: dict[str, Any] | None = None,
) -> tuple[bool, str, int]:
    """Apply R&D result to a product with risk/reward tiers and diminishing returns."""
    import math

    product = await session.get(Product, product_id)
    if product is None:
        return False, "产品不存在", 0

    safe_staff = max(0, min(extra_staff, MAX_EXTRA_RD_STAFF))

    special_multiplier = 1.0
    special_rep_bonus = 0
    special_quality_bonus = 0
    special_text = ""
    if special_effect:
        special_multiplier = max(1.0, float(special_effect.get("income_multiplier", 1.0)))
        special_rep_bonus = max(0, int(special_effect.get("reputation_bonus", 0)))
        special_quality_bonus = max(0, int(special_effect.get("quality_bonus", 0)))
        special_name = str(special_effect.get("name", "关键词触发"))
        special_flavor = str(special_effect.get("flavor_text", "")).strip()
        emoji_pack = str(special_effect.get("emoji_pack", "")).strip()
        soul_question = str(special_effect.get("soul_question", "")).strip()
        meme_lines = special_effect.get("meme_lines", [])
        if not isinstance(meme_lines, list):
            meme_lines = []
        special_text = (
            f"\n🎼 关键词触发: {special_name}\n"
            f"✨ 额外效果: 收益倍率×{special_multiplier:.2f} | 声望+{special_rep_bonus} | 品质+{special_quality_bonus}"
        )
        if emoji_pack:
            special_text += f"\n{emoji_pack} 春日影氛围已注入"
        if soul_question:
            special_text += f"\n🗣 灵魂句: {soul_question}"
        if special_flavor:
            special_text += f"\n📝 {special_flavor}"
        if meme_lines:
            special_text += "\n📌 梗提示:"
            for line in meme_lines[:2]:
                special_text += f"\n  · {line}"

    from services.product_service import MAX_PRODUCT_DAILY_INCOME

    # ── Risk/reward tiers based on score ──
    BASE_RD_INCOME = 300
    SCORE_FACTOR = 5.0
    DIMINISH_THRESHOLD = 50_000
    DIMINISH_RATE = 0.6

    quality_delta = 0
    tier_text = ""

    if score < 30:
        # 翻车：收入-3%（最低不低于初始值的50%）
        penalty = max(1, int(product.daily_income * 0.03))
        min_income = max(1, product.daily_income // 2)
        new_income = max(min_income, product.daily_income - penalty)
        income_change = new_income - product.daily_income  # negative
        product.daily_income = new_income
        product.quality = max(1, product.quality - 1)
        product.version += 1
        await session.flush()
        from services.company_service import update_daily_revenue
        await update_daily_revenue(session, product.company_id)

        rep = 1 + special_rep_bonus
        await add_reputation(session, owner_user_id, rep)
        await add_points(owner_user_id, max(1, score // 4), session=session)

        return True, (
            f"💥 方案翻车！市场反馈极差，产品口碑受损\n"
            f"评分: {score}/100\n"
            f"产品「{product.name}」日收入{income_change}  → {product.daily_income}"
            f"{special_text}"
        ), income_change

    # For scores >= 30, calculate additive boost
    raw_boost = BASE_RD_INCOME + int(score * SCORE_FACTOR)
    staff_mult = 1 + safe_staff * 0.03
    raw_boost = int(raw_boost * staff_mult * special_multiplier)

    # High-income diminishing returns
    if product.daily_income > DIMINISH_THRESHOLD:
        ratio = product.daily_income / DIMINISH_THRESHOLD
        diminish = max(0.05, 1.0 / (1 + DIMINISH_RATE * math.log(ratio)))
        raw_boost = max(1, int(raw_boost * diminish))

    # Apply tier multiplier
    if score < 50:
        # 平庸
        raw_boost = max(1, int(raw_boost * 0.3))
        quality_delta = 0
        tier_text = "😐 方案平庸，勉强维持现状"
    elif score < 70:
        # 可行
        quality_delta = 1
        tier_text = "✅ 方案可行，稳步推进中"
    elif score < 85:
        # 优秀
        raw_boost = int(raw_boost * 1.5)
        quality_delta = 2
        tier_text = "🌟 方案优秀！市场反响良好"
    else:
        # 卓越
        raw_boost = int(raw_boost * 2.0)
        quality_delta = 3
        tier_text = "🏆 方案卓越！引领行业新风向"

    income_increase = min(raw_boost, MAX_PRODUCT_DAILY_INCOME - product.daily_income)
    income_increase = max(0, income_increase)
    product.daily_income += income_increase
    product.quality = min(100, product.quality + quality_delta + special_quality_bonus)
    product.version += 1
    await session.flush()
    from services.company_service import update_daily_revenue
    await update_daily_revenue(session, product.company_id)

    rep = max(1, score // 5) + special_rep_bonus
    # 卓越额外声望
    if score >= 85:
        rep += 5
    await add_reputation(session, owner_user_id, rep)
    await add_points(owner_user_id, score // 2, session=session)

    return True, (
        f"{tier_text}\n"
        f"评分: {score}/100\n"
        f"产品「{product.name}」日收入+{income_increase} → {product.daily_income}"
        f"{special_text}"
    ), income_increase
