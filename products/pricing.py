"""
Price resolution (Phase 6.6).

Resolves the unit price for a product given the customer tier and quantity:
  1. If a quantity break applies (qty >= min_quantity, scoped to the tier or all tiers),
     use the best (lowest) applicable break price.
  2. Otherwise use the customer-tier list price (retail / semi-wholesale / wholesale).

Pure read helper — does not change any stored data or the checkout flow.
"""
from decimal import Decimal


def tier_price(product, customer_type):
    """The list price for a customer tier (defaults to retail).

    Falls back to retail if the product was never given a real wholesale/semi-wholesale
    price (0/unset) — a product that doesn't sell at that tier should never be charged
    for free just because the tier field was left blank.
    """
    if customer_type == 'wholesale' and product.price_wholesale:
        return product.price_wholesale
    if customer_type == 'semi_wholesale' and product.price_semi_wholesale:
        return product.price_semi_wholesale
    return product.price_retail


def resolve_price(product, customer_type, quantity):
    """Best unit price for `quantity` units at the given tier."""
    qty = Decimal(str(quantity or 0))
    base = tier_price(product, customer_type)

    best = base
    for br in product.price_breaks.all():
        # Break applies if it's unscoped or matches the tier, and the qty qualifies.
        if br.customer_type and br.customer_type != customer_type:
            continue
        if qty >= br.min_quantity and br.unit_price < best:
            best = br.unit_price
    return best
