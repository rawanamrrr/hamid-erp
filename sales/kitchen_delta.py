"""What changed on a check, so the kitchen is told about the additions only.

Editing a cashier invoice rebuilds its lines from scratch — every existing row is deleted
and recreated from the submitted cart. After that, every row in the database looks new,
so notifying the kitchen with "the order's items" hands it the whole check again: the
food already cooked and served comes back out of the printer alongside the one drink that
was actually added, with nothing to say which is which.

The only way to know what is genuinely new is to remember the composition BEFORE the
rebuild and compare. That is what this module does.
"""
from collections import defaultdict
from decimal import Decimal


def signature(item):
    """What makes two lines "the same thing" to a kitchen.

    Product, variant, modifiers and the note — quantity deliberately excluded, since a
    line going from 1 to 3 is the same thing in a larger amount, and the kitchen needs to
    hear about the difference rather than the total.
    """
    mods = tuple(sorted(
        (m.get('option', ''), m.get('quantity', 1))
        for m in (item.modifiers or []) if isinstance(m, dict)
    ))
    return (item.product_id, getattr(item, 'variant_id', None), mods, (item.note or '').strip())


def snapshot(items):
    """{signature: total quantity} for a set of lines."""
    totals = defaultdict(Decimal)
    for item in items:
        totals[signature(item)] += Decimal(str(item.quantity or 0))
    return dict(totals)


def additions(before, after_items):
    """Lines to send to the kitchen: what `after_items` has beyond the `before` snapshot.

    Returns [(item, quantity)] where quantity is only the added amount — so raising a
    line from 1 to 3 yields that line with a quantity of 2, not 3. Removals and
    unchanged lines produce nothing: the kitchen is never asked to un-cook anything, and
    a re-print of untouched food is exactly the problem this exists to prevent.
    """
    remaining = {key: Decimal(str(value)) for key, value in (before or {}).items()}
    out = []
    for item in after_items:
        key = signature(item)
        quantity = Decimal(str(item.quantity or 0))
        already = remaining.get(key, Decimal('0'))
        if already >= quantity:
            remaining[key] = already - quantity
            continue
        added = quantity - already
        remaining[key] = Decimal('0')
        if added > 0:
            out.append((item, added))
    return out
