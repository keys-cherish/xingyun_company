"""Boundary and equivalence-class tests for research completion logic."""

from __future__ import annotations

import datetime as dt
from unittest.mock import patch

from db.models import Company, ResearchProgress, User
from services.research_service import check_and_complete_research

from tests.helpers.async_db_case import AsyncDBTestCase
from tests.helpers.fake_redis import FakeRedis


class TestResearchLogic(AsyncDBTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.fake_redis = FakeRedis()

        async def _fake_get_redis():
            return self.fake_redis

        self._patcher = patch("services.user_service.get_redis", new=_fake_get_redis)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    async def _make_owner_and_company(self, session, tg_id: int) -> tuple[User, Company]:
        owner = User(tg_id=tg_id, tg_name=f"owner-{tg_id}", traffic=1_000_000, reputation=0)
        session.add(owner)
        await session.flush()
        company = Company(
            name=f"ResearchCo-{tg_id}",
            owner_id=owner.id,
            company_type="tech",
            total_funds=0,
            daily_revenue=0,
            level=1,
            employee_count=1,
        )
        session.add(company)
        await session.flush()
        return owner, company

    async def test_research_not_completed_before_duration_boundary(self):
        # Base duration is 3600, but tech company gets:
        # - research_speed_bonus = 0.20
        # - focus_bonus = 0.15 (basic_internet is in tech focus)
        # multiplier = 1.0 - 0.20 - 0.15 = 0.65
        # Effective duration = 3600 * 0.65 = 2340
        tech_duration = 2340
        now = dt.datetime.now(dt.timezone.utc)

        async with self.Session() as session:
            async with session.begin():
                _owner, company = await self._make_owner_and_company(session, 3001)
                rp = ResearchProgress(
                    company_id=company.id,
                    tech_id="basic_internet",
                    status="researching",
                    started_at=now - dt.timedelta(seconds=tech_duration - 5),
                )
                session.add(rp)
                await session.flush()

                completed = await check_and_complete_research(session, company.id)
                self.assertEqual(completed, [])

            updated = await session.get(ResearchProgress, rp.id)
            self.assertEqual(updated.status, "researching")

    async def test_research_completes_after_duration_and_accepts_naive_datetime(self):
        # Base duration is 3600, but tech company gets:
        # - research_speed_bonus = 0.20
        # - focus_bonus = 0.15 (basic_internet is in tech focus)
        # multiplier = 1.0 - 0.20 - 0.15 = 0.65
        # Effective duration = 3600 * 0.65 = 2340
        tech_duration = 2340
        now = dt.datetime.now(dt.timezone.utc)

        async with self.Session() as session:
            async with session.begin():
                owner, company = await self._make_owner_and_company(session, 3002)
                rp = ResearchProgress(
                    company_id=company.id,
                    tech_id="basic_internet",
                    status="researching",
                    started_at=now - dt.timedelta(seconds=tech_duration + 5),
                )
                session.add(rp)
                await session.flush()

                completed = await check_and_complete_research(session, company.id)
                self.assertGreaterEqual(len(completed), 1)

            updated_rp = await session.get(ResearchProgress, rp.id)
            updated_owner = await session.get(User, owner.id)
            self.assertEqual(updated_rp.status, "completed")
            self.assertIsNotNone(updated_rp.completed_at)
            self.assertGreater(updated_owner.reputation, 0)
