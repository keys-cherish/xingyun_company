"""General AI chat service for @mention interaction."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from config import settings

logger = logging.getLogger(__name__)
DEFAULT_AI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_CHAT_SYSTEM_PROMPT = (
    "你是“商业帝国”Telegram经营游戏机器人的AI助手。"
    "你必须始终使用简体中文、语气专业简洁、结论先行。"
    "围绕公司经营玩法提供可执行建议：科研、产品、员工、合作、商战、成本、道德、监管、景气周期。"
    "金额与收益统一使用“积分”表述，不使用MB/GB换算。"
    "当用户提问不完整时，先给最稳妥方案，再给最多2个可选策略。"
    "拒绝提供违规、攻击、破坏、越权、泄露密钥或管理员绕过方案。"
    "不编造未确认事实；不确定时明确说明并建议如何验证。"
)


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
        logger.warning("Invalid AI_EXTRA_HEADERS_JSON, ignored in chat service")
        return {}


def _extract_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        text_chunks: list[str] = []
        for item in content:
            if isinstance(item, dict):
                txt = item.get("text")
                if isinstance(txt, str):
                    text_chunks.append(txt.strip())
        return "\n".join(x for x in text_chunks if x).strip()
    return str(content).strip()


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


async def ask_ai_chat(prompt: str) -> str:
    """Call AI provider and return response text."""
    if not settings.ai_enabled or not settings.ai_api_key.strip():
        return "AI 功能未启用。"

    try:
        import httpx

        system_prompt = (
            (settings.ai_chat_system_prompt or "").strip()
            or (settings.ai_system_prompt or "").strip()
            or DEFAULT_CHAT_SYSTEM_PROMPT
        )
        model_name = (settings.ai_model or "").strip() or "gpt-4o-mini"
        completion_url = _normalize_completion_url(settings.ai_api_base_url)
        timeout = max(5, int(settings.ai_timeout_seconds))
        retry_times = max(0, int(settings.ai_max_retries))
        retry_backoff = max(0.2, float(settings.ai_retry_backoff_seconds))
        temperature = max(0.0, min(2.0, float(settings.ai_temperature)))
        top_p = max(0.0, min(1.0, float(settings.ai_top_p)))
        max_tokens = max(64, int(settings.ai_max_tokens))

        headers = {
            "Authorization": f"Bearer {settings.ai_api_key}",
            "Content-Type": "application/json",
        }
        headers.update(_parse_extra_headers(settings.ai_extra_headers_json))

        payload = {
            "model": model_name,
            "stream": False,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "temperature": temperature,
            "top_p": top_p,
            "max_tokens": max_tokens,
        }

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
        return content or "AI 暂时没有给出有效回复。"

    except Exception as exc:
        logger.warning("AI chat call failed: %s", exc)
        return "AI 服务暂时不可用，请稍后再试。"
