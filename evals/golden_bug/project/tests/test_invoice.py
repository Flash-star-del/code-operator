from __future__ import annotations

import pytest

from invoice import LineItem, calculate_invoice
from pricing import discounted_subtotal, line_subtotal


def test_no_discount_or_tax() -> None:
    totals = calculate_invoice(
        [LineItem("book", 1299, 2)], discount_percent=0, tax_percent=0
    )
    assert totals.subtotal_cents == 2598
    assert totals.discounted_subtotal_cents == 2598
    assert totals.tax_cents == 0
    assert totals.total_cents == 2598


def test_line_subtotal_rejects_invalid_quantity() -> None:
    with pytest.raises(ValueError, match="quantity"):
        line_subtotal(500, 0)


def test_discount_rounds_half_cent_up() -> None:
    assert discounted_subtotal(101, 50) == 50


def test_tax_uses_discounted_subtotal() -> None:
    totals = calculate_invoice(
        [LineItem("lamp", 1000, 1)], discount_percent=10, tax_percent=10
    )
    assert totals.discounted_subtotal_cents == 900
    assert totals.tax_cents == 90
    assert totals.total_cents == 990


def test_multi_item_discount_tax_and_rounding() -> None:
    totals = calculate_invoice(
        [LineItem("pen", 999, 3), LineItem("clip", 250, 2)],
        discount_percent=15,
        tax_percent=8,
    )
    assert totals.subtotal_cents == 3497
    assert totals.discounted_subtotal_cents == 2972
    assert totals.tax_cents == 238
    assert totals.total_cents == 3210
