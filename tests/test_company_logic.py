"""Boundary and equivalence-class tests for company core logic."""

from __future__ import annotations

from config import settings
from db.models import Company, User
from services.company_service import (
    create_company,
    get_company_employee_limit,
    get_level_info,
    get_max_level,
    upgrade_company,
)

from tests.helpers.async_db_case import AsyncDBTestCase


class TestCompanyLogic(AsyncDBTestCase):
    async def _new_user(self, session, tg_id: int, traffic: int = 200_000) -> User:
        user = User(tg_id=tg_id, tg_name=f"user-{tg_id}", traffic=traffic, reputation=0)
        session.add(user)
        await session.flush()
        return user

    async def test_create_company_invalid_type_equivalence_class(self):
        async with self.Session() as session:
            async with session.begin():
                owner = await self._new_user(session, 1001)
                company, _msg = await create_company(session, owner, "Alpha", "bad_type")
        self.assertIsNone(company)

    async def test_create_company_duplicate_name_boundary(self):
        async with self.Session() as session:
            async with session.begin():
                owner_1 = await self._new_user(session, 1002)
                owner_2 = await self._new_user(session, 1003)
                first, _ = await create_company(session, owner_1, "UniqueName", "tech")
                second, _ = await create_company(session, owner_2, "UniqueName", "tech")
        self.assertIsNotNone(first)
        self.assertIsNone(second)

    async def test_create_company_exact_cost_boundary(self):
        async with self.Session() as session:
            async with session.begin():
                owner = await self._new_user(session, 1004, traffic=settings.company_creation_cost)
                company, _ = await create_company(session, owner, "ExactCostCo", "tech")
                owner_id = owner.id

            refreshed_owner = await session.get(User, owner_id)
            self.assertIsNotNone(company)
            self.assertEqual(refreshed_owner.traffic, 0)
            self.assertEqual(company.total_funds, settings.company_creation_cost)

    async def test_create_company_initial_employee_default(self):
        async with self.Session() as session:
            async with session.begin():
                owner = await self._new_user(session, 1010)
                company, _ = await create_company(session, owner, "DefaultEmpCo", "tech")
        self.assertIsNotNone(company)
        self.assertEqual(company.employee_count, settings.base_employee_limit)

    def test_top_level_employee_limit_is_capped(self):
        max_level = get_max_level()
        top_limit = get_company_employee_limit(max_level, "manufacturing")
        self.assertEqual(top_limit, settings.max_employee_limit)

    async def test_upgrade_company_exact_funds_boundary(self):
        next_info = get_level_info(2)
        self.assertIsNotNone(next_info)
        cost = next_info["upgrade_cost"]

        async with self.Session() as session:
            async with session.begin():
                owner = await self._new_user(session, 1005)
                company = Company(
                    name="UpgradeEdge",
                    owner_id=owner.id,
                    company_type="tech",
                    level=1,
                    employee_count=2,
                    total_funds=cost,
                    daily_revenue=0,
                )
                session.add(company)
                await session.flush()
                company_id = company.id

                # 满足 Lv.2 需求: 1产品 + 1科技
                from db.models import Product, ResearchProgress
                product = Product(
                    company_id=company_id,
                    name="TestProd",
                    tech_id="basic",
                    version=1,
                    daily_income=100,
                    quality=10,
                )
                session.add(product)
                rp = ResearchProgress(
                    company_id=company_id,
                    tech_id="basic",
                    status="completed",
                )
                session.add(rp)
                await session.flush()

                ok, _msg = await upgrade_company(session, company_id)
                self.assertTrue(ok)

            updated = await session.get(Company, company_id)
            self.assertEqual(updated.level, 2)
            self.assertEqual(updated.total_funds, 0)

    async def test_upgrade_company_insufficient_funds_equivalence_class(self):
        next_info = get_level_info(2)
        self.assertIsNotNone(next_info)
        cost = next_info["upgrade_cost"]

        async with self.Session() as session:
            async with session.begin():
                owner = await self._new_user(session, 1006)
                company = Company(
                    name="NoMoneyUpgrade",
                    owner_id=owner.id,
                    company_type="tech",
                    level=1,
                    employee_count=2,
                    total_funds=cost - 1,
                    daily_revenue=0,
                )
                session.add(company)
                await session.flush()

                # 满足其他条件，只有资金不足
                from db.models import Product, ResearchProgress
                product = Product(
                    company_id=company.id,
                    name="TestProd2",
                    tech_id="basic",
                    version=1,
                    daily_income=100,
                    quality=10,
                )
                session.add(product)
                rp = ResearchProgress(
                    company_id=company.id,
                    tech_id="basic",
                    status="completed",
                )
                session.add(rp)
                await session.flush()

                ok, _msg = await upgrade_company(session, company.id)

        self.assertFalse(ok)

    async def test_upgrade_company_max_level_boundary(self):
        max_level = get_max_level()

        async with self.Session() as session:
            async with session.begin():
                owner = await self._new_user(session, 1007)
                company = Company(
                    name="MaxLevelCo",
                    owner_id=owner.id,
                    company_type="tech",
                    level=max_level,
                    employee_count=1,
                    total_funds=999_999_999,
                    daily_revenue=0,
                )
                session.add(company)
                await session.flush()
                ok, _msg = await upgrade_company(session, company.id)

        self.assertFalse(ok)
