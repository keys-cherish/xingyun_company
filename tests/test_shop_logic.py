"""Regression tests for shop item effects."""

from __future__ import annotations

import datetime as dt
from unittest.mock import patch

from sqlalchemy import func as sqlfunc, select

from db.models import Company, ResearchProgress, User
from services.shop_service import buy_item

from tests.helpers.async_db_case import AsyncDBTestCase
from tests.helpers.fake_redis import FakeRedis


class TestShopLogic(AsyncDBTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.fake_redis = FakeRedis()

        async def _fake_get_redis():
            return self.fake_redis

        self._shop_redis_patcher = patch("services.shop_service.get_redis", new=_fake_get_redis)
        self._shop_redis_patcher.start()
        self.addCleanup(self._shop_redis_patcher.stop)

    async def _new_owner_and_company(self, session, tg_id: int, *, funds: int = 20_000) -> tuple[User, Company]:
        user = User(tg_id=tg_id, tg_name=f"user-{tg_id}", traffic=0, reputation=0)
        session.add(user)
        await session.flush()

        company = Company(
            name=f"ShopTest-{tg_id}",
            owner_id=user.id,
            company_type="tech",
            total_funds=funds,
            daily_revenue=0,
            level=1,
            employee_count=5,
        )
        session.add(company)
        await session.flush()
        return user, company

    async def test_speed_research_item_halves_remaining_time(self):
        async with self.Session() as session:
            async with session.begin():
                user, company = await self._new_owner_and_company(session, 7101, funds=12_000)
                now_db = (await session.execute(select(sqlfunc.now()))).scalar()
                if now_db is None:
                    now_db = dt.datetime.utcnow()
                if getattr(now_db, "tzinfo", None):
                    now_db = now_db.replace(tzinfo=None)
                started_at = now_db.replace(microsecond=0) - dt.timedelta(hours=1)
                rp = ResearchProgress(
                    company_id=company.id,
                    tech_id="t_speed",
                    status="researching",
                    started_at=started_at,
                )
                session.add(rp)
                await session.flush()

                tech_tree = {"t_speed": {"duration_seconds": 4 * 3600}}
                with patch("services.research_service._load_tech_tree", return_value=tech_tree):
                    ok, _msg = await buy_item(session, user.tg_id, company.id, "speed_research")
                    self.assertTrue(ok)

                await session.refresh(rp)

                now = (await session.execute(select(sqlfunc.now()))).scalar()
                if now is None:
                    now = dt.datetime.utcnow()
                if getattr(now, "tzinfo", None):
                    now = now.replace(tzinfo=None)
                before_elapsed = max(0.0, (now - started_at).total_seconds())
                after_elapsed = max(0.0, (now - rp.started_at).total_seconds())
                duration = 4 * 3600
                before_remaining = max(0.0, duration - before_elapsed)
                after_remaining = max(0.0, duration - after_elapsed)

                self.assertLess(after_remaining, before_remaining)
                self.assertAlmostEqual(after_remaining, before_remaining / 2.0, delta=3.0)
