"""Tests for the settlement pipeline."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from services.settlement.breakdowns import (
    CostBreakdown,
    IncomeBreakdown,
    PenaltyBreakdown,
    SettlementResult,
)


class TestIncomeBreakdown(unittest.TestCase):
    """Tests for IncomeBreakdown dataclass."""

    def test_default_values(self):
        """Test default values are all zero."""
        breakdown = IncomeBreakdown()
        self.assertEqual(breakdown.product_income, 0)
        self.assertEqual(breakdown.level_bonus, 0)
        self.assertEqual(breakdown.cooperation_bonus, 0)
        self.assertEqual(breakdown.realestate_income, 0)
        self.assertEqual(breakdown.reputation_buff, 0)
        self.assertEqual(breakdown.ad_boost, 0)
        self.assertEqual(breakdown.shop_buff, 0)
        self.assertEqual(breakdown.totalwar_buff, 0)
        self.assertEqual(breakdown.type_bonus, 0)
        self.assertEqual(breakdown.employee_income, 0)

    def test_total_calculation(self):
        """Test total property calculates correctly."""
        breakdown = IncomeBreakdown(
            product_income=10000,
            level_bonus=500,
            cooperation_bonus=1000,
            realestate_income=2000,
            reputation_buff=300,
            ad_boost=200,
            shop_buff=100,
            totalwar_buff=50,
            type_bonus=150,
            employee_income=1500,
        )
        expected_total = 10000 + 500 + 1000 + 2000 + 300 + 200 + 100 + 50 + 150 + 1500
        self.assertEqual(breakdown.total, expected_total)

    def test_total_with_partial_values(self):
        """Test total with only some values set."""
        breakdown = IncomeBreakdown(
            product_income=5000,
            employee_income=1000,
        )
        self.assertEqual(breakdown.total, 6000)


class TestPenaltyBreakdown(unittest.TestCase):
    """Tests for PenaltyBreakdown dataclass."""

    def test_default_values(self):
        """Test default values are all zero."""
        breakdown = PenaltyBreakdown()
        self.assertEqual(breakdown.rename_penalty, 0)
        self.assertEqual(breakdown.battle_debuff, 0)
        self.assertEqual(breakdown.roadshow_penalty, 0)

    def test_total_calculation(self):
        """Test total property calculates correctly."""
        breakdown = PenaltyBreakdown(
            rename_penalty=1000,
            battle_debuff=500,
            roadshow_penalty=200,
        )
        self.assertEqual(breakdown.total, 1700)


class TestCostBreakdown(unittest.TestCase):
    """Tests for CostBreakdown dataclass."""

    def test_default_values(self):
        """Test default values are all zero."""
        breakdown = CostBreakdown()
        self.assertEqual(breakdown.tax, 0)
        self.assertEqual(breakdown.salary, 0)
        self.assertEqual(breakdown.social_insurance, 0)
        self.assertEqual(breakdown.base_operating, 0)
        self.assertEqual(breakdown.office_cost, 0)
        self.assertEqual(breakdown.training_cost, 0)
        self.assertEqual(breakdown.regulation_cost, 0)
        self.assertEqual(breakdown.insurance_cost, 0)
        self.assertEqual(breakdown.work_cost_adjust, 0)
        self.assertEqual(breakdown.culture_maintenance, 0)
        self.assertEqual(breakdown.regulation_fine, 0)
        self.assertEqual(breakdown.type_cost_modifier, 0)

    def test_total_calculation(self):
        """Test total property calculates correctly."""
        breakdown = CostBreakdown(
            tax=1000,
            salary=5000,
            social_insurance=500,
            base_operating=300,
            office_cost=200,
            training_cost=100,
            regulation_cost=50,
            insurance_cost=100,
            work_cost_adjust=50,
            culture_maintenance=25,
            regulation_fine=100,
            type_cost_modifier=-200,  # Negative modifier (discount)
        )
        expected_total = 1000 + 5000 + 500 + 300 + 200 + 100 + 50 + 100 + 50 + 25 + 100 - 200
        self.assertEqual(breakdown.total, expected_total)


class TestSettlementResult(unittest.TestCase):
    """Tests for SettlementResult dataclass."""

    def test_creation(self):
        """Test creating a SettlementResult."""
        income = IncomeBreakdown(product_income=10000)
        penalties = PenaltyBreakdown(rename_penalty=100)
        costs = CostBreakdown(tax=500)

        result = SettlementResult(
            income=income,
            penalties=penalties,
            costs=costs,
            gross_income=10000,
            net_income=9900,
            profit=9400,
        )

        self.assertEqual(result.income.product_income, 10000)
        self.assertEqual(result.penalties.rename_penalty, 100)
        self.assertEqual(result.costs.tax, 500)
        self.assertEqual(result.gross_income, 10000)
        self.assertEqual(result.net_income, 9900)
        self.assertEqual(result.profit, 9400)
        self.assertEqual(result.events, [])

    def test_events_default_empty(self):
        """Test events default to empty list."""
        result = SettlementResult(
            income=IncomeBreakdown(),
            penalties=PenaltyBreakdown(),
            costs=CostBreakdown(),
            gross_income=0,
            net_income=0,
            profit=0,
        )
        self.assertEqual(result.events, [])
        self.assertIsInstance(result.events, list)

    def test_events_can_be_provided(self):
        """Test events can be provided."""
        events = ["Event 1", "Event 2"]
        result = SettlementResult(
            income=IncomeBreakdown(),
            penalties=PenaltyBreakdown(),
            costs=CostBreakdown(),
            gross_income=0,
            net_income=0,
            profit=0,
            events=events,
        )
        self.assertEqual(result.events, ["Event 1", "Event 2"])


class TestComputeCosts(unittest.TestCase):
    """Tests for compute_costs function - pure function tests."""

    def test_compute_costs_pure_function(self):
        """Test that compute_costs is a pure function (same input -> same output)."""
        from services.settlement.pipeline import compute_costs

        income = IncomeBreakdown(product_income=100000, employee_income=5000)

        # Mock company and profile
        mock_company = MagicMock()
        mock_company.employee_count = 10

        mock_profile = MagicMock()

        type_info = {"cost_bonus": -0.1}  # 10% cost reduction
        extra_costs = {
            "office_cost": 100,
            "training_cost": 50,
            "regulation_cost": 30,
            "insurance_cost": 20,
            "work_cost_adjust": 10,
            "culture_maintenance": 5,
        }
        regulation_fine = 0

        # Call twice with same inputs
        costs1 = compute_costs(income, mock_company, mock_profile, type_info, extra_costs, regulation_fine)
        costs2 = compute_costs(income, mock_company, mock_profile, type_info, extra_costs, regulation_fine)

        # Results should be identical
        self.assertEqual(costs1.tax, costs2.tax)
        self.assertEqual(costs1.salary, costs2.salary)
        self.assertEqual(costs1.social_insurance, costs2.social_insurance)
        self.assertEqual(costs1.base_operating, costs2.base_operating)
        self.assertEqual(costs1.total, costs2.total)

    def test_compute_costs_with_fine(self):
        """Test compute_costs includes regulation fine."""
        from services.settlement.pipeline import compute_costs

        income = IncomeBreakdown(product_income=50000)
        mock_company = MagicMock()
        mock_company.employee_count = 5
        mock_profile = MagicMock()
        type_info = {}
        extra_costs = {
            "office_cost": 0,
            "training_cost": 0,
            "regulation_cost": 0,
            "insurance_cost": 0,
            "work_cost_adjust": 0,
            "culture_maintenance": 0,
        }
        regulation_fine = 1000

        costs = compute_costs(income, mock_company, mock_profile, type_info, extra_costs, regulation_fine)

        self.assertEqual(costs.regulation_fine, 1000)
        # Total should include the fine
        self.assertIn(1000, [costs.regulation_fine])


class TestBreakdownEquality(unittest.TestCase):
    """Tests for breakdown equality and immutability."""

    def test_income_breakdown_equality(self):
        """Test two IncomeBreakdowns with same values are equal."""
        b1 = IncomeBreakdown(product_income=1000, level_bonus=100)
        b2 = IncomeBreakdown(product_income=1000, level_bonus=100)
        self.assertEqual(b1, b2)

    def test_income_breakdown_inequality(self):
        """Test two IncomeBreakdowns with different values are not equal."""
        b1 = IncomeBreakdown(product_income=1000)
        b2 = IncomeBreakdown(product_income=2000)
        self.assertNotEqual(b1, b2)

    def test_penalty_breakdown_equality(self):
        """Test two PenaltyBreakdowns with same values are equal."""
        p1 = PenaltyBreakdown(rename_penalty=500)
        p2 = PenaltyBreakdown(rename_penalty=500)
        self.assertEqual(p1, p2)

    def test_cost_breakdown_equality(self):
        """Test two CostBreakdowns with same values are equal."""
        c1 = CostBreakdown(tax=1000, salary=5000)
        c2 = CostBreakdown(tax=1000, salary=5000)
        self.assertEqual(c1, c2)


if __name__ == "__main__":
    unittest.main()
