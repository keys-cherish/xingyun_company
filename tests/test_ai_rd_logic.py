"""产品迭代逻辑测试 — 概率提升收入（简化版）。"""

from __future__ import annotations

from unittest.mock import patch

from db.models import Company, Product, User
from services.ai_rd_service import quick_iterate, get_rd_cost, _get_fallback_blurb, TIERS

from tests.helpers.async_db_case import AsyncDBTestCase
from tests.helpers.fake_redis import FakeRedis


class TestAiRdLogic(AsyncDBTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.fake_redis = FakeRedis()

        async def _fake_get_redis():
            return self.fake_redis

        self._patcher = patch("services.user_service.get_redis", new=_fake_get_redis)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    async def test_quick_iterate_always_increases_income(self):
        """迭代后收入只增不减。"""
        async with self.Session() as session:
            async with session.begin():
                owner = User(tg_id=8101, tg_name="owner-8101", traffic=0, reputation=10)
                session.add(owner)
                await session.flush()

                company = Company(
                    name="IterCo",
                    owner_id=owner.id,
                    company_type="tech",
                    total_funds=100000,
                    daily_revenue=0,
                    level=1,
                    employee_count=10,
                )
                session.add(company)
                await session.flush()

                product = Product(
                    company_id=company.id,
                    name="IterProduct",
                    tech_id="basic_internet",
                    version=1,
                    daily_income=1000,
                    quality=40,
                )
                session.add(product)
                await session.flush()

                original_income = product.daily_income
                ok, msg, income_increase, tier_key = await quick_iterate(
                    session, product.id, owner.id,
                )

                self.assertTrue(ok)
                self.assertGreater(income_increase, 0)
                self.assertEqual(product.daily_income, original_income + income_increase)
                self.assertEqual(product.version, 2)
                self.assertIn(tier_key, ["small", "medium", "large", "critical"])

    async def test_quick_iterate_updates_quality_and_reputation(self):
        """迭代后品质和声望都应增加。"""
        async with self.Session() as session:
            async with session.begin():
                owner = User(tg_id=8102, tg_name="owner-8102", traffic=0, reputation=10)
                session.add(owner)
                await session.flush()

                company = Company(
                    name="QualCo",
                    owner_id=owner.id,
                    company_type="tech",
                    total_funds=100000,
                    daily_revenue=0,
                    level=1,
                    employee_count=10,
                )
                session.add(company)
                await session.flush()

                product = Product(
                    company_id=company.id,
                    name="QualProduct",
                    tech_id="basic_internet",
                    version=1,
                    daily_income=1000,
                    quality=40,
                )
                session.add(product)
                await session.flush()

                original_quality = product.quality
                original_reputation = owner.reputation

                ok, msg, income_increase, tier_key = await quick_iterate(
                    session, product.id, owner.id,
                )

                self.assertTrue(ok)
                self.assertGreater(product.quality, original_quality)

            updated_owner = await session.get(User, owner.id)
            self.assertGreater(updated_owner.reputation, original_reputation)

    async def test_get_rd_cost_scales_with_version(self):
        """迭代费用应随版本递增。"""
        from unittest.mock import MagicMock
        p1 = MagicMock(spec=Product, version=1)
        p5 = MagicMock(spec=Product, version=5)
        p10 = MagicMock(spec=Product, version=10)

        cost1 = get_rd_cost(p1)
        cost5 = get_rd_cost(p5)
        cost10 = get_rd_cost(p10)

        self.assertGreater(cost5, cost1)
        self.assertGreater(cost10, cost5)

    async def test_fallback_blurb_returns_string_for_each_tier(self):
        """内置段子对每个档位都能返回字符串。"""
        for tier in TIERS:
            tier_key = tier[4]
            blurb = _get_fallback_blurb(tier_key)
            self.assertIsInstance(blurb, str)
            self.assertGreater(len(blurb), 5)

    async def test_quick_iterate_nonexistent_product(self):
        """不存在的产品应返回失败。"""
        async with self.Session() as session:
            async with session.begin():
                owner = User(tg_id=8103, tg_name="owner-8103", traffic=0, reputation=0)
                session.add(owner)
                await session.flush()

                ok, msg, income_increase, tier_key = await quick_iterate(
                    session, 99999, owner.id,
                )
                self.assertFalse(ok)
                self.assertEqual(income_increase, 0)
