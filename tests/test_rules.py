"""Tests for the Rule system and service rules."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from tests.helpers.async_db_case import AsyncDBTestCase
from utils.rules import Rule, RuleViolation, check_rules_parallel, check_rules_sequential


class TestRuleViolation(unittest.TestCase):
    """Tests for RuleViolation dataclass."""

    def test_create_violation(self):
        """Test creating a RuleViolation."""
        violation = RuleViolation(
            code="TEST_CODE",
            actual=100,
            expected=200,
            message="Test message",
        )
        self.assertEqual(violation.code, "TEST_CODE")
        self.assertEqual(violation.actual, 100)
        self.assertEqual(violation.expected, 200)
        self.assertEqual(violation.message, "Test message")

    def test_violation_is_frozen(self):
        """Test that RuleViolation is immutable."""
        violation = RuleViolation(
            code="TEST",
            actual=1,
            expected=2,
            message="test",
        )
        with self.assertRaises(Exception):
            violation.code = "CHANGED"


class TestRule(unittest.TestCase):
    """Tests for Rule dataclass."""

    def test_create_rule(self):
        """Test creating a Rule."""
        async def dummy_check(**_):
            return None

        rule = Rule(code="TEST_RULE", check=dummy_check)
        self.assertEqual(rule.code, "TEST_RULE")
        self.assertEqual(rule.check, dummy_check)


class TestCheckRulesSequential(AsyncDBTestCase):
    """Tests for check_rules_sequential function."""

    async def test_all_pass(self):
        """Test when all rules pass."""
        async def pass_rule(**_):
            return None

        rules = [
            Rule("RULE1", pass_rule),
            Rule("RULE2", pass_rule),
            Rule("RULE3", pass_rule),
        ]
        result = await check_rules_sequential(rules, ctx_value=123)
        self.assertIsNone(result)

    async def test_first_fails(self):
        """Test when first rule fails."""
        async def fail_rule(**_):
            return RuleViolation("FAIL", 1, 2, "Failed")

        async def pass_rule(**_):
            return None

        rules = [
            Rule("RULE1", fail_rule),
            Rule("RULE2", pass_rule),
        ]
        result = await check_rules_sequential(rules)
        self.assertIsNotNone(result)
        self.assertEqual(result.code, "FAIL")

    async def test_middle_fails(self):
        """Test when middle rule fails."""
        call_order = []

        async def pass_rule(name, **_):
            call_order.append(name)
            return None

        async def fail_rule(name, **_):
            call_order.append(name)
            return RuleViolation("FAIL", 1, 2, "Failed")

        rules = [
            Rule("RULE1", lambda **ctx: pass_rule("rule1", **ctx)),
            Rule("RULE2", lambda **ctx: fail_rule("rule2", **ctx)),
            Rule("RULE3", lambda **ctx: pass_rule("rule3", **ctx)),
        ]
        result = await check_rules_sequential(rules)
        self.assertIsNotNone(result)
        self.assertEqual(result.code, "FAIL")
        # Rule3 should not be called
        self.assertEqual(call_order, ["rule1", "rule2"])

    async def test_context_passed(self):
        """Test that context is passed to check functions."""
        received_ctx = {}

        async def capture_ctx(**ctx):
            received_ctx.update(ctx)
            return None

        rules = [Rule("RULE1", capture_ctx)]
        await check_rules_sequential(rules, foo="bar", num=42)
        self.assertEqual(received_ctx["foo"], "bar")
        self.assertEqual(received_ctx["num"], 42)


class TestCheckRulesParallel(AsyncDBTestCase):
    """Tests for check_rules_parallel function."""

    async def test_all_pass(self):
        """Test when all rules pass."""
        async def pass_rule(**_):
            return None

        rules = [
            Rule("RULE1", pass_rule),
            Rule("RULE2", pass_rule),
        ]
        result = await check_rules_parallel(rules)
        self.assertEqual(result, [])

    async def test_one_fails(self):
        """Test when one rule fails."""
        async def fail_rule(**_):
            return RuleViolation("FAIL1", 1, 2, "Failed 1")

        async def pass_rule(**_):
            return None

        rules = [
            Rule("RULE1", fail_rule),
            Rule("RULE2", pass_rule),
        ]
        result = await check_rules_parallel(rules)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].code, "FAIL1")

    async def test_multiple_fail(self):
        """Test when multiple rules fail."""
        async def fail_rule1(**_):
            return RuleViolation("FAIL1", 1, 2, "Failed 1")

        async def fail_rule2(**_):
            return RuleViolation("FAIL2", 3, 4, "Failed 2")

        async def pass_rule(**_):
            return None

        rules = [
            Rule("RULE1", fail_rule1),
            Rule("RULE2", pass_rule),
            Rule("RULE3", fail_rule2),
        ]
        result = await check_rules_parallel(rules)
        self.assertEqual(len(result), 2)
        codes = [v.code for v in result]
        self.assertIn("FAIL1", codes)
        self.assertIn("FAIL2", codes)

    async def test_all_checked(self):
        """Test that all rules are checked even when some fail."""
        call_count = 0

        async def counting_rule(**_):
            nonlocal call_count
            call_count += 1
            return RuleViolation("FAIL", 1, 2, "Failed") if call_count <= 2 else None

        rules = [
            Rule("RULE1", counting_rule),
            Rule("RULE2", counting_rule),
            Rule("RULE3", counting_rule),
        ]
        result = await check_rules_parallel(rules)
        # All rules should be checked
        self.assertEqual(call_count, 3)
        # Two failures
        self.assertEqual(len(result), 2)


class TestCompanyRules(AsyncDBTestCase):
    """Tests for company upgrade rules."""

    async def test_check_company_exists_not_found(self):
        """Test check_company_exists when company doesn't exist."""
        from services.rules.company_rules import check_company_exists

        async with self.Session() as session:
            violation = await check_company_exists(
                session=session,
                company_id=99999,
            )
            self.assertIsNotNone(violation)
            self.assertEqual(violation.code, "COMPANY_NOT_FOUND")

    async def test_check_company_exists_found(self):
        """Test check_company_exists when company exists."""
        from db.models import Company, User
        from services.rules.company_rules import check_company_exists

        async with self.Session() as session:
            async with session.begin():
                user = User(tg_id=12345, tg_name="testuser")
                session.add(user)
                await session.flush()

                company = Company(
                    name="Test Co",
                    company_type="tech",
                    owner_id=user.id,
                    total_funds=10000,
                )
                session.add(company)
                await session.flush()

                violation = await check_company_exists(
                    session=session,
                    company_id=company.id,
                )
                self.assertIsNone(violation)

    async def test_check_not_max_level_at_max(self):
        """Test check_not_max_level when company is at max level."""
        from db.models import Company, User
        from services.rules.company_rules import check_not_max_level
        from services.company_service import get_max_level

        async with self.Session() as session:
            async with session.begin():
                user = User(tg_id=12346, tg_name="testuser2")
                session.add(user)
                await session.flush()

                max_level = get_max_level()
                company = Company(
                    name="Max Level Co",
                    company_type="tech",
                    owner_id=user.id,
                    total_funds=10000,
                    level=max_level,
                )
                session.add(company)
                await session.flush()

                violation = await check_not_max_level(
                    session=session,
                    company_id=company.id,
                )
                self.assertIsNotNone(violation)
                self.assertEqual(violation.code, "MAX_LEVEL")

    async def test_check_upgrade_funds_insufficient(self):
        """Test check_upgrade_funds when funds are insufficient."""
        from db.models import Company, User
        from services.rules.company_rules import check_upgrade_funds

        async with self.Session() as session:
            async with session.begin():
                user = User(tg_id=12347, tg_name="testuser3")
                session.add(user)
                await session.flush()

                company = Company(
                    name="Poor Co",
                    company_type="tech",
                    owner_id=user.id,
                    total_funds=100,
                )
                session.add(company)
                await session.flush()

                violation = await check_upgrade_funds(
                    session=session,
                    company_id=company.id,
                    next_info={"upgrade_cost": 10000},
                )
                self.assertIsNotNone(violation)
                self.assertEqual(violation.code, "INSUFFICIENT_FUNDS")
                self.assertEqual(violation.actual, 100)
                self.assertEqual(violation.expected, 10000)

    async def test_check_upgrade_funds_sufficient(self):
        """Test check_upgrade_funds when funds are sufficient."""
        from db.models import Company, User
        from services.rules.company_rules import check_upgrade_funds

        async with self.Session() as session:
            async with session.begin():
                user = User(tg_id=12348, tg_name="testuser4")
                session.add(user)
                await session.flush()

                company = Company(
                    name="Rich Co",
                    company_type="tech",
                    owner_id=user.id,
                    total_funds=100000,
                )
                session.add(company)
                await session.flush()

                violation = await check_upgrade_funds(
                    session=session,
                    company_id=company.id,
                    next_info={"upgrade_cost": 10000},
                )
                self.assertIsNone(violation)


class TestProductRules(AsyncDBTestCase):
    """Tests for product rules."""

    async def test_check_product_template_valid_invalid(self):
        """Test check_product_template_valid with invalid template."""
        from services.rules.product_rules import check_product_template_valid

        violation = await check_product_template_valid(
            templates={"valid_key": {}},
            product_key="invalid_key",
        )
        self.assertIsNotNone(violation)
        self.assertEqual(violation.code, "INVALID_TEMPLATE")

    async def test_check_product_template_valid_valid(self):
        """Test check_product_template_valid with valid template."""
        from services.rules.product_rules import check_product_template_valid

        violation = await check_product_template_valid(
            templates={"valid_key": {"name": "Test"}},
            product_key="valid_key",
        )
        self.assertIsNone(violation)

    async def test_check_product_name_valid_empty(self):
        """Test check_product_name_valid with empty name."""
        from services.rules.product_rules import check_product_name_valid

        violation = await check_product_name_valid(name="")
        self.assertIsNotNone(violation)
        self.assertEqual(violation.code, "INVALID_NAME")

    async def test_check_product_name_valid_ok(self):
        """Test check_product_name_valid with valid name."""
        from services.rules.product_rules import check_product_name_valid

        violation = await check_product_name_valid(name="Valid Product Name")
        self.assertIsNone(violation)


class TestResearchRules(AsyncDBTestCase):
    """Tests for research rules."""

    async def test_check_tech_valid_invalid(self):
        """Test check_tech_valid with invalid tech."""
        from services.rules.research_rules import check_tech_valid

        violation = await check_tech_valid(
            tech_tree={"valid_tech": {}},
            tech_id="invalid_tech",
        )
        self.assertIsNotNone(violation)
        self.assertEqual(violation.code, "INVALID_TECH")

    async def test_check_tech_valid_valid(self):
        """Test check_tech_valid with valid tech."""
        from services.rules.research_rules import check_tech_valid

        violation = await check_tech_valid(
            tech_tree={"valid_tech": {"name": "Test Tech"}},
            tech_id="valid_tech",
        )
        self.assertIsNone(violation)

    async def test_check_prerequisites_not_met(self):
        """Test check_prerequisites when prereq not completed."""
        from services.rules.research_rules import check_prerequisites

        violation = await check_prerequisites(
            tech_tree={
                "advanced_tech": {
                    "name": "Advanced Tech",
                    "prerequisites": ["basic_tech"],
                },
                "basic_tech": {"name": "Basic Tech"},
            },
            tech_id="advanced_tech",
            completed_techs=set(),
        )
        self.assertIsNotNone(violation)
        self.assertEqual(violation.code, "PREREQUISITE_NOT_MET")
        self.assertIn("Basic Tech", violation.message)

    async def test_check_prerequisites_met(self):
        """Test check_prerequisites when prereq completed."""
        from services.rules.research_rules import check_prerequisites

        violation = await check_prerequisites(
            tech_tree={
                "advanced_tech": {
                    "name": "Advanced Tech",
                    "prerequisites": ["basic_tech"],
                },
                "basic_tech": {"name": "Basic Tech"},
            },
            tech_id="advanced_tech",
            completed_techs={"basic_tech"},
        )
        self.assertIsNone(violation)


class TestBattleRules(AsyncDBTestCase):
    """Tests for battle rules."""

    async def test_check_strategy_valid_invalid(self):
        """Test check_strategy_valid with invalid strategy."""
        from services.rules.battle_rules import check_strategy_valid

        violation = await check_strategy_valid(
            strategy=None,
            attacker_strategy_raw="invalid_strategy",
            valid_strategy_hint="valid strategies",
        )
        self.assertIsNotNone(violation)
        self.assertEqual(violation.code, "INVALID_STRATEGY")

    async def test_check_strategy_valid_valid(self):
        """Test check_strategy_valid with valid strategy."""
        from services.rules.battle_rules import check_strategy_valid

        # Create a mock strategy object (non-None)
        mock_strategy = MagicMock()
        violation = await check_strategy_valid(
            strategy=mock_strategy,
            attacker_strategy_raw="balanced",
            valid_strategy_hint="valid strategies",
        )
        self.assertIsNone(violation)

    async def test_check_not_self_battle_same(self):
        """Test check_not_self_battle when same user."""
        from services.rules.battle_rules import check_not_self_battle

        violation = await check_not_self_battle(
            attacker_tg_id=12345,
            defender_tg_id=12345,
        )
        self.assertIsNotNone(violation)
        self.assertEqual(violation.code, "SELF_BATTLE")

    async def test_check_not_self_battle_different(self):
        """Test check_not_self_battle when different users."""
        from services.rules.battle_rules import check_not_self_battle

        violation = await check_not_self_battle(
            attacker_tg_id=12345,
            defender_tg_id=67890,
        )
        self.assertIsNone(violation)

    async def test_check_attacker_registered_not(self):
        """Test check_attacker_registered when not registered."""
        from services.rules.battle_rules import check_attacker_registered

        violation = await check_attacker_registered(attacker_user=None)
        self.assertIsNotNone(violation)
        self.assertEqual(violation.code, "ATTACKER_NOT_REGISTERED")

    async def test_check_attacker_registered_ok(self):
        """Test check_attacker_registered when registered."""
        from services.rules.battle_rules import check_attacker_registered

        mock_user = MagicMock()
        violation = await check_attacker_registered(attacker_user=mock_user)
        self.assertIsNone(violation)

    async def test_check_attacker_has_company_none(self):
        """Test check_attacker_has_company when no company."""
        from services.rules.battle_rules import check_attacker_has_company

        violation = await check_attacker_has_company(attacker_companies=[])
        self.assertIsNotNone(violation)
        self.assertEqual(violation.code, "ATTACKER_NO_COMPANY")

    async def test_check_attacker_has_company_ok(self):
        """Test check_attacker_has_company when has company."""
        from services.rules.battle_rules import check_attacker_has_company

        violation = await check_attacker_has_company(attacker_companies=[MagicMock()])
        self.assertIsNone(violation)


if __name__ == "__main__":
    unittest.main()
