"""Boundary and equivalence-class tests for exchange logic."""

from __future__ import annotations

from unittest.mock import patch

from db.models import User
from services.user_service import (
    exchange_credits_for_quota,
    exchange_points_for_traffic,
    exchange_quota_for_credits,
    get_quota_mb,
)

from tests.helpers.async_db_case import AsyncDBTestCase
from tests.helpers.fake_redis import FakeRedis


class TestExchangeLogic(AsyncDBTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.fake_redis = FakeRedis()

        async def _fake_get_redis():
            return self.fake_redis

        self._patcher = patch("services.user_service.get_redis", new=_fake_get_redis)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    async def _new_user(self, session, tg_id: int, traffic: int = 10_000) -> User:
        user = User(tg_id=tg_id, tg_name=f"user-{tg_id}", traffic=traffic, reputation=0)
        session.add(user)
        await session.flush()
        return user

    async def test_credits_to_quota_amount_must_be_positive_boundary(self):
        async with self.Session() as session:
            async with session.begin():
                await self._new_user(session, 2001)
                ok, _msg = await exchange_credits_for_quota(session, 2001, 0)
        self.assertFalse(ok)

    async def test_credits_to_quota_below_rate_equivalence_class(self):
        async with self.Session() as session:
            async with session.begin():
                await self._new_user(session, 2002, traffic=500)
                with patch("services.user_service.get_credit_to_quota_rate", return_value=100):
                    ok, _msg = await exchange_credits_for_quota(session, 2002, 99)
        self.assertFalse(ok)

    async def test_credits_to_quota_exact_rate_boundary(self):
        async with self.Session() as session:
            async with session.begin():
                user = await self._new_user(session, 2003, traffic=100)
                with patch("services.user_service.get_credit_to_quota_rate", return_value=100):
                    ok, _msg = await exchange_credits_for_quota(session, 2003, 100)
                    self.assertTrue(ok)
                    self.assertEqual(user.traffic, 0)

            quota = await get_quota_mb(2003)
            self.assertEqual(quota, 1)

    async def test_credits_to_quota_streak_bonus_every_third_exchange_boundary(self):
        async with self.Session() as session:
            async with session.begin():
                user = await self._new_user(session, 2004, traffic=1_000)
                with patch("services.user_service.get_credit_to_quota_rate", return_value=100):
                    for _ in range(3):
                        ok, _ = await exchange_credits_for_quota(session, 2004, 100)
                        self.assertTrue(ok)
                self.assertEqual(user.traffic, 700)

            quota = await get_quota_mb(2004)
            # 1MB + 1MB + (1MB + bonus 1MB)
            self.assertEqual(quota, 4)

    async def test_quota_to_credits_amount_must_be_positive_boundary(self):
        async with self.Session() as session:
            async with session.begin():
                await self._new_user(session, 2005, traffic=0)
                ok, _msg = await exchange_quota_for_credits(session, 2005, 0)
        self.assertFalse(ok)

    async def test_quota_to_credits_insufficient_quota_equivalence_class(self):
        async with self.Session() as session:
            async with session.begin():
                await self._new_user(session, 2006, traffic=0)
                await self.fake_redis.set("quota:2006", "3")
                with patch("services.user_service.get_credit_to_quota_rate", return_value=100):
                    ok, _msg = await exchange_quota_for_credits(session, 2006, 10)
        self.assertFalse(ok)

    async def test_quota_to_credits_success_equivalence_class(self):
        async with self.Session() as session:
            async with session.begin():
                user = await self._new_user(session, 2007, traffic=0)
                await self.fake_redis.set("quota:2007", "15")
                with patch("services.user_service.get_credit_to_quota_rate", return_value=100):
                    ok, _msg = await exchange_quota_for_credits(session, 2007, 10)
                    self.assertTrue(ok)
                    # reverse rate = 80
                    self.assertEqual(user.traffic, 800)

            remain_quota = await get_quota_mb(2007)
            self.assertEqual(remain_quota, 5)

    async def test_points_to_credits_equivalence_classes(self):
        async with self.Session() as session:
            async with session.begin():
                user = await self._new_user(session, 2008, traffic=0)
                await self.fake_redis.set("points:2008", "9")
                ok_low, _ = await exchange_points_for_traffic(session, 2008, 9)
                self.assertFalse(ok_low)
                self.assertEqual(user.traffic, 0)

                await self.fake_redis.set("points:2008", "10")
                ok_valid, _ = await exchange_points_for_traffic(session, 2008, 10)
                self.assertTrue(ok_valid)
                self.assertEqual(user.traffic, 1)
