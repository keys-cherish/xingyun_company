"""Research / tech tree system."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from db.models import ResearchProgress
from services.user_service import add_reputation, add_traffic, add_points

_tech_tree: dict | None = None


def _load_tech_tree() -> dict:
    global _tech_tree
    if _tech_tree is None:
        path = Path(__file__).resolve().parent.parent / "game_data" / "tech_tree.json"
        with open(path, encoding="utf-8") as f:
            _tech_tree = json.load(f)["nodes"]
    return _tech_tree


async def get_completed_techs(session: AsyncSession, company_id: int) -> list[str]:
    result = await session.execute(
        select(ResearchProgress).where(
            ResearchProgress.company_id == company_id,
            ResearchProgress.status == "completed",
        )
    )
    return [r.tech_id for r in result.scalars().all()]


async def get_in_progress_research(session: AsyncSession, company_id: int) -> list[ResearchProgress]:
    result = await session.execute(
        select(ResearchProgress).where(
            ResearchProgress.company_id == company_id,
            ResearchProgress.status == "researching",
        )
    )
    return list(result.scalars().all())


async def get_available_techs(session: AsyncSession, company_id: int) -> list[dict]:
    """Return techs whose prerequisites are met and not yet researched."""
    tree = _load_tech_tree()
    completed = set(await get_completed_techs(session, company_id))

    # Also exclude in-progress
    in_progress_rows = await get_in_progress_research(session, company_id)
    in_progress_ids = {r.tech_id for r in in_progress_rows}

    available = []
    for tech_id, info in tree.items():
        if tech_id in completed or tech_id in in_progress_ids:
            continue
        prereqs = info.get("prerequisites", [])
        if all(p in completed for p in prereqs):
            available.append({"tech_id": tech_id, **info})
    return available


async def start_research(
    session: AsyncSession,
    company_id: int,
    owner_user_id: int,
    tech_id: str,
) -> tuple[bool, str]:
    """Start researching a technology. Deducts traffic from the owner."""
    tree = _load_tech_tree()
    if tech_id not in tree:
        return False, "无效的科研项目"

    tech = tree[tech_id]

    # Check prerequisites
    completed = set(await get_completed_techs(session, company_id))
    for prereq in tech.get("prerequisites", []):
        if prereq not in completed:
            prereq_name = tree.get(prereq, {}).get("name", prereq)
            return False, f"需要先完成前置科研: {prereq_name}"

    # Check not already researching or done
    existing = await session.execute(
        select(ResearchProgress).where(
            ResearchProgress.company_id == company_id,
            ResearchProgress.tech_id == tech_id,
        )
    )
    if existing.scalar_one_or_none():
        return False, "该科研已开始或已完成"

    # Deduct cost
    cost = tech.get("cost", settings.base_research_cost)
    ok = await add_traffic(session, owner_user_id, -cost)
    if not ok:
        return False, f"流量不足，需要{cost}MB"

    rp = ResearchProgress(
        company_id=company_id,
        tech_id=tech_id,
        status="researching",
    )
    session.add(rp)
    await session.flush()

    # Grant points for starting research
    await add_points(owner_user_id, 5, session=session)

    return True, f"开始研究「{tech['name']}」，需要{tech.get('duration_seconds', 3600)}秒完成"


async def check_and_complete_research(session: AsyncSession, company_id: int) -> list[str]:
    """Check all in-progress research; complete those past duration. Returns list of completed tech names."""
    tree = _load_tech_tree()
    in_progress = await get_in_progress_research(session, company_id)
    now = dt.datetime.now(dt.timezone.utc)
    completed_names = []

    for rp in in_progress:
        tech = tree.get(rp.tech_id)
        if tech is None:
            continue
        duration = dt.timedelta(seconds=tech.get("duration_seconds", settings.base_research_seconds))
        if now - rp.started_at >= duration:
            rp.status = "completed"
            rp.completed_at = now
            completed_names.append(tech["name"])

            # Grant reputation to owner
            from db.models import Company
            company = await session.get(Company, company_id)
            if company:
                rep = tech.get("reputation_reward", settings.reputation_per_research)
                await add_reputation(session, company.owner_id, rep)
                await add_points(company.owner_id, rep, session=session)

    await session.flush()
    return completed_names


def get_tech_tree_display() -> list[dict]:
    """Return the full tech tree for display purposes."""
    tree = _load_tech_tree()
    return [{"tech_id": k, **v} for k, v in tree.items()]
