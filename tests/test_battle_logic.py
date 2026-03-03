"""Battle logic tests: strategy, underdog upset, bilateral damage, cooldown scaling."""

from __future__ import annotations

from unittest.mock import patch

from db.models import Company, User
from services.battle_service import BATTLE_COOLDOWN_SECONDS, battle

from tests.helpers.async_db_case import AsyncDBTestCase
from tests.helpers.fake_redis import FakeRedis


class TestBattleLogic(AsyncDBTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.fake_redis = FakeRedis()

        async def _fake_get_redis():
            return self.fake_redis

        self._patcher = patch("services.battle_service.get_redis", new=_fake_get_redis)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

        self._bounty_patcher = patch("services.bounty_service.get_redis", new=_fake_get_redis)
        self._bounty_patcher.start()
        self.addCleanup(self._bounty_patcher.stop)

        self._rules_patcher = patch("services.rules.battle_rules.get_redis", new=_fake_get_redis)
        self._rules_patcher.start()
        self.addCleanup(self._rules_patcher.stop)

        self._ops_patcher = patch("services.operations_service.get_redis", new=_fake_get_redis)
        self._ops_patcher.start()
        self.addCleanup(self._ops_patcher.stop)

        # 新建公司总是在训练模式，测试中关闭训练模式以验证完整逻辑
        self._training_patcher = patch(
            "services.battle_service._is_training_mode", return_value=False
        )
        self._training_patcher.start()
        self.addCleanup(self._training_patcher.stop)

    async def _make_player(
        self,
        session,
        *,
        tg_id: int,
        name: str,
        funds: int,
        revenue: int,
        employees: int,
        level: int,
        reputation: int = 100,
    ) -> tuple[User, Company]:
        user = User(tg_id=tg_id, tg_name=f"user-{tg_id}", traffic=0, reputation=reputation)
        session.add(user)
        await session.flush()

        company = Company(
            name=name,
            company_type="tech",
            owner_id=user.id,
            total_funds=funds,
            daily_revenue=revenue,
            level=level,
            employee_count=employees,
        )
        session.add(company)
        await session.flush()

        # Seed points for battle cost
        await self.fake_redis.set(f"points:{tg_id}", "10000")

        return user, company

    async def test_battle_rejects_invalid_strategy_equivalence_class(self):
        async with self.Session() as session:
            async with session.begin():
                await self._make_player(
                    session, tg_id=5101, name="A", funds=50_000, revenue=1_000, employees=10, level=3
                )
                await self._make_player(
                    session, tg_id=5102, name="B", funds=50_000, revenue=1_000, employees=10, level=3
                )
                ok, msg = await battle(session, 5101, 5102, attacker_strategy="乱填战术")

        self.assertFalse(ok)
        self.assertIn("无效战术", msg)

    async def test_battle_applies_bilateral_damage_to_both_sides_boundary(self):
        async with self.Session() as session:
            async with session.begin():
                a_user, a_company = await self._make_player(
                    session,
                    tg_id=5201,
                    name="AttackerCo",
                    funds=220_000,
                    revenue=4_000,
                    employees=24,
                    level=4,
                    reputation=120,
                )
                d_user, d_company = await self._make_player(
                    session,
                    tg_id=5202,
                    name="DefenderCo",
                    funds=190_000,
                    revenue=3_500,
                    employees=21,
                    level=4,
                    reputation=120,
                )
                a_emp_before = a_company.employee_count
                d_emp_before = d_company.employee_count

                # Keep this deterministic and disable black-swan in this case.
                with patch("services.battle_service.random.random", return_value=1.0), patch(
                    "services.battle_service.random.uniform", side_effect=lambda a, b: (a + b) / 2
                ):
                    ok, msg = await battle(session, 5201, 5202, attacker_strategy="稳扎稳打")
                self.assertTrue(ok)

            a_user_after = await session.get(User, a_user.id)
            d_user_after = await session.get(User, d_user.id)
            a_company_after = await session.get(Company, a_company.id)
            d_company_after = await session.get(Company, d_company.id)

        self.assertIn("双边战损", msg)
        self.assertLess(a_company_after.employee_count, a_emp_before)
        self.assertLess(d_company_after.employee_count, d_emp_before)
        self.assertLess(a_user_after.reputation, 120)
        self.assertLess(d_user_after.reputation, 120)

    async def test_underdog_can_upset_with_black_swan_equivalence_class(self):
        async with self.Session() as session:
            async with session.begin():
                _a_user, _a_company = await self._make_player(
                    session,
                    tg_id=5301,
                    name="SmallCo",
                    funds=20_000,
                    revenue=500,
                    employees=8,
                    level=2,
                )
                _d_user, _d_company = await self._make_player(
                    session,
                    tg_id=5302,
                    name="BigCo",
                    funds=60_000,
                    revenue=1_200,
                    employees=15,
                    level=3,
                )

                # Force black-swan + favorable rolls for weak side.
                with patch("services.battle_service.random.random", return_value=0.0), patch(
                    "services.battle_service.random.uniform",
                    side_effect=[0.35, 0.85, 1.18, 0.88, 0.0, 0.0],
                ):
                    ok, msg = await battle(session, 5301, 5302, attacker_strategy="奇袭渗透")

        self.assertTrue(ok)
        self.assertIn("黑天鹅事件", msg)
        self.assertIn("胜者: SmallCo", msg)

    async def test_cooldown_is_longer_when_strong_side_stomps_boundary(self):
        async with self.Session() as session:
            async with session.begin():
                await self._make_player(
                    session,
                    tg_id=5401,
                    name="TitanCo",
                    funds=600_000,
                    revenue=8_000,
                    employees=60,
                    level=6,
                )
                await self._make_player(
                    session,
                    tg_id=5402,
                    name="TinyCo",
                    funds=20_000,
                    revenue=300,
                    employees=6,
                    level=1,
                )

                with patch("services.battle_service.random.random", return_value=1.0), patch(
                    "services.battle_service.random.uniform", side_effect=lambda a, b: (a + b) / 2
                ):
                    ok, _msg = await battle(session, 5401, 5402, attacker_strategy="激进营销")
                self.assertTrue(ok)

                ttl = await self.fake_redis.ttl("battle_cd:5401")

        self.assertGreater(ttl, BATTLE_COOLDOWN_SECONDS)
