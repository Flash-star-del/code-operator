from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from pricing import (
    discounted_subtotal,
    line_subtotal,
    require_percent,
    round_ratio_half_up,
)


@dataclass(frozen=True)
class LineItem:
    sku: str
    unit_price_cents: int
    quantity: int


@dataclass(frozen=True)
class InvoiceTotals:
    subtotal_cents: int
    discounted_subtotal_cents: int
    tax_cents: int
    total_cents: int


def calculate_invoice(
    items: Sequence[LineItem],
    *,
    discount_percent: int,
    tax_percent: int,
) -> InvoiceTotals:
    subtotal_cents = sum(
        line_subtotal(item.unit_price_cents, item.quantity) for item in items
    )
    discounted_cents = discounted_subtotal(subtotal_cents, discount_percent)
    require_percent(tax_percent, "tax_percent")
    tax_cents = round_ratio_half_up(subtotal_cents * tax_percent, 100)
    return InvoiceTotals(
        subtotal_cents=subtotal_cents,
        discounted_subtotal_cents=discounted_cents,
        tax_cents=tax_cents,
        total_cents=discounted_cents + tax_cents,
    )
