"""Regression tests for dividend distribution accounting."""

from __future__ import annotations

from unittest.mock import patch

from config import settings
from db.models import Company, Shareholder, User
from services.dividend_service import distribute_dividends

from tests.helpers.async_db_case import AsyncDBTestCase
from tests.helpers.fake_redis import FakeRedis


class TestDividendDistribution(AsyncDBTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.fake_redis = FakeRedis()

        async def _fake_get_redis():
            return self.fake_redis

        self._patcher = patch("services.user_service.get_redis", new=_fake_get_redis)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    async def _seed_company_with_shareholders(self, session, funds: int) -> tuple[Company, User, User]:
        owner = User(tg_id=7101, tg_name="owner-7101", traffic=0, reputation=0)
        investor = User(tg_id=7102, tg_name="investor-7102", traffic=0, reputation=0)
        session.add_all([owner, investor])
        await session.flush()

        company = Company(
            name=f"DividendCo-{funds}",
            owner_id=owner.id,
            company_type="tech",
            total_funds=funds,
            daily_revenue=0,
            level=1,
            employee_count=5,
        )
        session.add(company)
        await session.flush()

        session.add_all(
            [
                Shareholder(company_id=company.id, user_id=owner.id, shares=60.0, invested_amount=6000),
                Shareholder(company_id=company.id, user_id=investor.id, shares=40.0, invested_amount=4000),
            ]
        )
        await session.flush()
        return company, owner, investor

    async def test_dividend_distribution_should_deduct_company_funds(self):
        """Dividend payout must reduce company funds to avoid inflation."""
        async with self.Session() as session:
            async with session.begin():
                company, owner, investor = await self._seed_company_with_shareholders(session, funds=2_000)
                pool = int(1_000 * settings.dividend_pct)
                distributions = await distribute_dividends(session, company, profit=1_000)

                await session.refresh(company)
                await session.refresh(owner)
                await session.refresh(investor)

                self.assertEqual(sum(amount for _, amount in distributions), pool)
                self.assertEqual(company.total_funds, 2_000 - pool)
                self.assertEqual(owner.traffic, int(pool * 0.6))
                self.assertEqual(investor.traffic, int(pool * 0.4))

    async def test_dividend_should_skip_when_company_cannot_afford_pool(self):
        """When company funds are insufficient, dividend payout should be skipped."""
        async with self.Session() as session:
            async with session.begin():
                company, owner, investor = await self._seed_company_with_shareholders(session, funds=100)
                distributions = await distribute_dividends(session, company, profit=1_000)

                await session.refresh(company)
                await session.refresh(owner)
                await session.refresh(investor)

                self.assertEqual(distributions, [])
                self.assertEqual(company.total_funds, 100)
                self.assertEqual(owner.traffic, 0)
                self.assertEqual(investor.traffic, 0)
