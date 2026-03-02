"""Roadshow behavior tests: daily limit and satire branch."""

from __future__ import annotations

from unittest.mock import patch

from sqlalchemy import select

from config import settings
from db.models import Company, Roadshow, User
from services.roadshow_service import do_roadshow
from tests.helpers.async_db_case import AsyncDBTestCase
from tests.helpers.fake_redis import FakeRedis


class TestRoadshowLogic(AsyncDBTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.fake_redis = FakeRedis()

        async def _fake_get_redis():
            return self.fake_redis

        self._patchers = [
            patch("services.roadshow_service.get_redis", new=_fake_get_redis),
            patch("services.user_service.get_redis", new=_fake_get_redis),
            patch("services.shop_service.get_redis", new=_fake_get_redis),
        ]
        for patcher in self._patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    async def _new_user_company(self, session, *, tg_id: int, funds: int) -> tuple[User, Company]:
        user = User(tg_id=tg_id, tg_name=f"user-{tg_id}", traffic=0, reputation=0)
        session.add(user)
        await session.flush()

        company = Company(
            name=f"company-{tg_id}",
            owner_id=user.id,
            total_funds=funds,
            daily_revenue=1000,
        )
        session.add(company)
        await session.flush()
        return user, company

    async def test_daily_once_blocks_second_roadshow(self):
        async with self.Session() as session:
            async with session.begin():
                user, company = await self._new_user_company(session, tg_id=5101, funds=20_000)
                with patch("services.roadshow_service.random.random", return_value=0.99):
                    ok1, _msg1 = await do_roadshow(session, company.id, user.id)
                    self.assertTrue(ok1)

                    ok2, msg2 = await do_roadshow(session, company.id, user.id)
                    self.assertFalse(ok2)
                    self.assertIn("明天再来", msg2)

    async def test_satire_score_has_no_positive_bonus_and_sets_penalty(self):
        async with self.Session() as session:
            async with session.begin():
                user, company = await self._new_user_company(session, tg_id=5102, funds=20_000)
                start_funds = company.total_funds

                with patch("services.roadshow_service.random.random", return_value=0.0):
                    with patch("services.roadshow_service.random.choice", side_effect=lambda seq: seq[0]):
                        ok, msg = await do_roadshow(session, company.id, user.id)

                self.assertTrue(ok)
                self.assertIn("114514/100", msg)
                self.assertIn("不提供任何正向加成", msg)

                await session.refresh(company)
                await session.refresh(user)
                self.assertEqual(company.total_funds, start_funds - settings.roadshow_cost)
                self.assertEqual(user.reputation, 0)

                penalty = await self.fake_redis.get(f"roadshow_penalty:{company.id}")
                self.assertIsNotNone(penalty)
                self.assertGreater(float(penalty), 0)

                roadshow_record = (
                    await session.execute(select(Roadshow).where(Roadshow.company_id == company.id))
                ).scalars().one()
                self.assertEqual(roadshow_record.bonus, 0)
                self.assertEqual(roadshow_record.reputation_gained, 0)
