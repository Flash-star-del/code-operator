from __future__ import annotations


def _require_non_negative(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


def require_percent(value: int, name: str) -> None:
    _require_non_negative(value, name)
    if value > 100:
        raise ValueError(f"{name} must be between 0 and 100")


def round_ratio_half_up(numerator: int, denominator: int) -> int:
    _require_non_negative(numerator, "numerator")
    if isinstance(denominator, bool) or not isinstance(denominator, int) or denominator <= 0:
        raise ValueError("denominator must be a positive integer")
    quotient, remainder = divmod(numerator, denominator)
    return quotient + int(remainder * 2 >= denominator)


def line_subtotal(unit_price_cents: int, quantity: int) -> int:
    _require_non_negative(unit_price_cents, "unit_price_cents")
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        raise ValueError("quantity must be a positive integer")
    return unit_price_cents * quantity


def discounted_subtotal(subtotal_cents: int, discount_percent: int) -> int:
    _require_non_negative(subtotal_cents, "subtotal_cents")
    require_percent(discount_percent, "discount_percent")
    discount_cents = subtotal_cents * discount_percent // 100
    return subtotal_cents - discount_cents
