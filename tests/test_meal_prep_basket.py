"""Synthetic tests for the optional package-rounded basket optimizer."""

from decimal import Decimal

import pytest

from tools.meal_prep_basket import optimize, scale_requirements


def test_optimizer_balances_package_rounding_and_extra_store_cost():
    payload = {
        "currency": "EUR",
        "shopping_date": "2026-06-15",
        "extra_store_min_savings": 4,
        "ingredients": [
            {
                "name": "Fictional oats",
                "required_amount": 750,
                "unit": "g",
                "offers": [
                    {"store": "Store A", "product": "A oats", "package_unit": "g",
                     "package_amount": 500, "price": 2.5, "quality_equivalent": True},
                    {"store": "Store B", "product": "B oats", "package_unit": "g",
                     "package_amount": 1000, "price": 3, "quality_equivalent": True},
                ],
            },
            {
                "name": "Fictional beans",
                "required_amount": 600,
                "unit": "g",
                "offers": [
                    {"store": "Store A", "product": "A beans", "package_unit": "g",
                     "package_amount": 400, "price": 2, "quality_equivalent": True},
                    {"store": "Store B", "product": "B beans", "package_unit": "g",
                     "package_amount": 600, "price": 5, "quality_equivalent": True},
                ],
            },
        ],
    }
    result = optimize(payload)
    assert result["ok"] is True
    assert result["recommended"]["stores"] == ["Store B"]
    assert result["recommended"]["total"] == 8.0
    assert result["absolute_cheapest"]["total"] == 7.0


def test_scaling_normalizes_units_and_rejects_nonfinite_values():
    scaled = scale_requirements(
        {"ingredients": [
            {"name": "Fictional rice", "amount": 0.5, "unit": "kg"},
            {"name": "Fictional eggs", "amount": 2, "unit": "piece"},
        ]},
        Decimal("2"),
        Decimal("5"),
    )
    assert scaled["ingredients"] == [
        {"name": "Fictional rice", "required_amount": 1250, "unit": "g"},
        {"name": "Fictional eggs", "required_amount": 5, "unit": "piece"},
    ]
    with pytest.raises(ValueError, match="finite"):
        scale_requirements(
            {"ingredients": [{"name": "Bad", "amount": "NaN", "unit": "g"}]},
            Decimal("1"),
            Decimal("1"),
        )
