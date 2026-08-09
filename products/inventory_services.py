"""
inventory_services.py
=====================
Production-grade inventory service functions.

ARCHITECTURE NOTES:
- FIFO governs physical stock deduction order (oldest batch first).
- Weighted average is only informational (Product.cost_price).
- COGS is tracked per-transaction via StockTransaction.cost_price and OrderItem.cost_price.
- All deductions are atomic with select_for_update() to prevent race conditions.
"""

from decimal import Decimal
from django.db import transaction as db_transaction
from django.db.models import Sum
from django.utils import timezone


# ──────────────────────────────────────────────
#  HIGH-LEVEL STOCK SERVICE (single entry point)
# ──────────────────────────────────────────────
# Every flow that moves stock (POS sale, refund, void, purchase, transfer,
# adjustment, manufacturing) MUST go through these functions. They are the only
# code allowed to mutate StockBatch + WarehouseStock together, guaranteeing the
# invariant:   Σ StockBatch.current_quantity  ==  WarehouseStock.quantity
# for every (product, warehouse). Callers must NOT touch WarehouseStock directly.

def _resync_warehouse_stock(product, warehouse):
    """Set WarehouseStock.quantity = Σ batch current_quantity for this pair.

    This is the mechanism that keeps the two ledgers identical. Saving the
    WarehouseStock row also fires the post_save signal that refreshes the
    cached Product.stock_quantity total across all warehouses.
    """
    from products.models import StockBatch, WarehouseStock

    batch_sum = (
        StockBatch.objects
        .filter(product=product, warehouse=warehouse)
        .aggregate(s=Sum('current_quantity'))['s'] or Decimal('0.00')
    )
    ws, _ = WarehouseStock.objects.get_or_create(product=product, warehouse=warehouse)
    ws.quantity = Decimal(str(batch_sum))
    ws.save()  # triggers Product.calculate_total_stock() via signal
    return ws.quantity


def available_quantity(product, warehouse, *, include_expired=False):
    """Physical sellable quantity = Σ batch current_quantity (optionally excluding expired)."""
    from products.models import StockBatch

    qs = StockBatch.objects.filter(product=product, warehouse=warehouse, current_quantity__gt=0)
    if not include_expired:
        today = timezone.now().date()
        qs = qs.exclude(expiry_date__lt=today)
    return qs.aggregate(s=Sum('current_quantity'))['s'] or Decimal('0.00')


def issue_stock(product, warehouse, quantity, *, user=None, reference='', note='',
                transaction_type='OUT', allow_negative=False, block_expired=True,
                log=True, unit_price=None, discount=None):
    """Remove `quantity` (in main units) from stock using FEFO order.

    - FEFO: earliest expiry first, then oldest entry. Expired batches are skipped
      when block_expired=True (they are never auto-sold).
    - Updates batches, then resyncs WarehouseStock from the batch sum (divergence-proof).
    - Optionally logs a StockTransaction with the weighted cost (COGS).

    Returns: weighted_cost (Decimal) of the deducted units.
    Raises: ValueError if insufficient stock and allow_negative=False.
    """
    from products.models import StockBatch, StockTransaction

    if quantity is None or quantity <= 0:
        raise ValueError("كمية الخصم يجب أن تكون أكبر من صفر.")

    with db_transaction.atomic():
        batches = list(
            StockBatch.objects
            .select_for_update()
            .filter(product=product, warehouse=warehouse, current_quantity__gt=0)
            .order_by('expiry_date', 'created_at')  # FEFO
        )

        today = timezone.now().date()
        if block_expired:
            sellable = [b for b in batches if not (b.expiry_date and b.expiry_date < today)]
        else:
            sellable = batches

        total_available = sum((b.current_quantity for b in sellable), Decimal('0.00'))
        if total_available < quantity and not allow_negative:
            expired_note = ""
            if block_expired and total_available < sum((b.current_quantity for b in batches), Decimal('0.00')):
                expired_note = " (يوجد كمية منتهية الصلاحية غير قابلة للبيع)"
            raise ValueError(
                f"الكمية غير متوفرة في '{warehouse.name}' للصنف: {product.name} "
                f"(المطلوب: {quantity}, المتاح: {total_available}){expired_note}"
            )

        remaining = quantity
        total_cost_value = Decimal('0.00')
        covered = Decimal('0.00')

        for batch in sellable:
            if remaining <= 0:
                break
            take = min(batch.current_quantity, remaining)
            total_cost_value += take * batch.purchase_price
            batch.current_quantity -= take
            batch.save()
            remaining -= take
            covered += take

        # allow_negative path: portion not covered by batches is costed at product cost
        if remaining > 0:
            fallback_cost = product.cost_price or Decimal('0.00')
            total_cost_value += remaining * fallback_cost
            covered += remaining  # for weighted-cost denominator

        weighted_cost = (total_cost_value / quantity) if quantity > 0 else Decimal('0.00')

        _resync_warehouse_stock(product, warehouse)

        if log:
            StockTransaction.objects.create(
                product=product,
                warehouse=warehouse,
                transaction_type=transaction_type,
                quantity=quantity,
                unit_price=(unit_price if unit_price is not None else Decimal('0.00')),
                cost_price=weighted_cost,
                discount=(discount if discount is not None else Decimal('0.00')),
                reference_number=reference,
                note=note,
                created_by=user,
            )

        return weighted_cost


def restore_stock(product, warehouse, quantity, *, user=None, reference='', note='',
                  transaction_type='RET_IN', cost_price=None, log=True):
    """Return `quantity` back to stock (LIFO into existing batches, capped at
    initial_quantity; overflow goes to the newest batch; creates a batch if none).

    Updates batches, resyncs WarehouseStock, and optionally logs a StockTransaction.
    Used by: sales refunds, order void/edit, purchase-invoice reversal.
    """
    from products.models import StockBatch, StockTransaction

    if quantity is None or quantity <= 0:
        return Decimal('0.00')

    with db_transaction.atomic():
        batches = list(
            StockBatch.objects
            .select_for_update()
            .filter(product=product, warehouse=warehouse)
            .order_by('-created_at')  # LIFO
        )

        remaining = quantity
        for batch in batches:
            if remaining <= 0:
                break
            space = batch.initial_quantity - batch.current_quantity
            if space <= 0:
                continue
            put = min(space, remaining)
            batch.current_quantity += put
            batch.save()
            remaining -= put

        if remaining > 0:
            if batches:
                newest = batches[0]
                # widen the cap so the returned units are not lost
                newest.current_quantity += remaining
                newest.initial_quantity = max(newest.initial_quantity, newest.current_quantity)
                newest.save()
            else:
                # No batch exists (legacy product): create a reconciliation batch
                StockBatch.objects.create(
                    product=product,
                    warehouse=warehouse,
                    purchase_price=(cost_price if cost_price is not None else (product.cost_price or Decimal('0.00'))),
                    initial_quantity=remaining,
                    current_quantity=remaining,
                    batch_number='RETURN',
                )

        _resync_warehouse_stock(product, warehouse)

        if log:
            StockTransaction.objects.create(
                product=product,
                warehouse=warehouse,
                transaction_type=transaction_type,
                quantity=quantity,
                unit_price=Decimal('0.00'),
                cost_price=(cost_price if cost_price is not None else (product.cost_price or Decimal('0.00'))),
                reference_number=reference,
                note=note,
                created_by=user,
            )

        return quantity


def adjust_to(product, warehouse, target_qty, *, user=None, reference='', note=''):
    """Set a product's stock in a warehouse to `target_qty` (Phase 5.1 stocktake).

    Computes the delta and applies it through the same batch+WarehouseStock machinery as
    sales/returns (logging an ADJ movement), so the invariant Σbatches == WarehouseStock
    is preserved. Returns the signed delta applied.
    """
    from products.models import WarehouseStock

    target = Decimal(str(target_qty or 0))
    if target < 0:
        target = Decimal('0.00')
    ws = WarehouseStock.objects.filter(product=product, warehouse=warehouse).first()
    current = ws.quantity if ws else Decimal('0.00')
    delta = target - current
    if delta == 0:
        return Decimal('0.00')

    if delta > 0:
        restore_stock(product, warehouse, delta, user=user, transaction_type='ADJ',
                      reference=reference, note=note or 'تسوية جرد (زيادة)')
    else:
        issue_stock(product, warehouse, -delta, user=user, transaction_type='ADJ',
                    reference=reference, note=note or 'تسوية جرد (عجز)',
                    block_expired=False, allow_negative=True)
    return delta


def reconcile_product_warehouse(product, warehouse):
    """One-time data repair: make Σ batches == WarehouseStock.quantity, treating
    WarehouseStock as the authoritative physical count.

    - shortfall (batches < ws): add a synthetic batch at product cost for the gap
    - surplus  (batches > ws): trim newest batches down until they match
    Returns the delta applied to batches (positive=added, negative=trimmed).
    """
    from products.models import StockBatch, WarehouseStock

    ws = WarehouseStock.objects.filter(product=product, warehouse=warehouse).first()
    target = ws.quantity if ws else Decimal('0.00')

    with db_transaction.atomic():
        batches = list(
            StockBatch.objects.select_for_update()
            .filter(product=product, warehouse=warehouse)
            .order_by('-created_at')
        )
        batch_sum = sum((b.current_quantity for b in batches), Decimal('0.00'))
        delta = Decimal(str(target)) - batch_sum

        if delta > 0:
            StockBatch.objects.create(
                product=product,
                warehouse=warehouse,
                purchase_price=product.cost_price or Decimal('0.00'),
                initial_quantity=delta,
                current_quantity=delta,
                batch_number='RECONCILE',
            )
        elif delta < 0:
            to_trim = -delta
            for b in batches:  # newest first
                if to_trim <= 0:
                    break
                trim = min(b.current_quantity, to_trim)
                b.current_quantity -= trim
                b.save()
                to_trim -= trim

        return delta



def _suggest_new_sale_price(product, old_cost, new_cost):
    """Layer 2 'purchases.update_sale_price_on_receipt': suggest a new retail price that
    preserves the product's existing markup over cost, given the new receipt cost.
    Returns None if there's nothing sensible to suggest (no prior cost/price to base a
    markup on, or the suggestion wouldn't meaningfully change anything)."""
    old_cost = Decimal(str(old_cost or 0))
    new_cost = Decimal(str(new_cost or 0))
    old_retail = product.price_retail or Decimal('0')
    if old_cost <= 0 or old_retail <= 0 or new_cost <= 0:
        return None
    markup_ratio = old_retail / old_cost
    suggested_retail = (new_cost * markup_ratio).quantize(Decimal('0.01'))
    if abs(suggested_retail - old_retail) < Decimal('0.01'):
        return None
    return {
        'product_id': product.id, 'product_name': product.name,
        'old_cost': float(old_cost), 'new_cost': float(new_cost),
        'old_price': float(old_retail), 'suggested_price': float(suggested_retail),
    }


def apply_purchase_invoice_stock(invoice, user=None):
    """
    Apply stock from a confirmed purchase invoice.
    Creates StockBatch and StockTransaction records, updates WarehouseStock,
    and updates weighted average cost on each product.

    Safe against double-application via is_stock_applied flag + select_for_update.

    Returns (applied: bool, price_suggestions: list) — suggestions are only populated when
    'purchases.update_sale_price_on_receipt' is on (see _suggest_new_sale_price docstring).
    """
    from products.models import (
        StockBatch, StockTransaction, WarehouseStock, SupplierProduct, PurchaseInvoice
    )

    with db_transaction.atomic():
        # Lock and re-read the invoice to prevent double-apply
        locked_invoice = PurchaseInvoice.objects.select_for_update().get(pk=invoice.pk)

        if locked_invoice.is_stock_applied:
            # Already applied — idempotent exit
            return False, []

        supplier = locked_invoice.supplier
        warehouse = locked_invoice.warehouse

        from settings.policies import get_policy
        suggest_prices = get_policy('purchases.update_sale_price_on_receipt')
        price_suggestions = []

        # Phase 7.2: allocate landed cost (freight/customs) across lines by line value,
        # so each batch's cost includes its share. landed_cost 0 leaves costs unchanged.
        # The header-level discount is allocated the same way but in the OPPOSITE
        # direction — it lowers what was actually paid per unit, so it must lower the
        # batch cost too, or inventory valuation/COGS stay overstated on every
        # discounted purchase (net_amount on the invoice already accounts for it; the
        # per-unit cost basis fed into StockBatch/weighted-average previously didn't).
        landed = Decimal(str(getattr(locked_invoice, 'landed_cost', 0) or 0))
        discount = Decimal(str(getattr(locked_invoice, 'discount', 0) or 0))
        items_list = list(locked_invoice.items.all())
        total_value = sum((it.quantity * it.unit_price for it in items_list), Decimal('0.00'))

        for item in items_list:
            product = item.product
            qty = item.quantity
            bonus_qty = item.bonus_quantity
            unit_price = item.unit_price
            total_qty = qty + bonus_qty

            # Effective unit cost = price + this line's share of landed cost, minus this
            # line's share of the header discount, per received unit.
            effective_cost = unit_price
            if total_value > 0 and total_qty > 0:
                line_value = qty * unit_price
                if landed > 0:
                    line_landed = landed * (line_value / total_value)
                    effective_cost += line_landed / total_qty
                if discount > 0:
                    line_discount = discount * (line_value / total_value)
                    effective_cost -= line_discount / total_qty
                effective_cost = max(effective_cost, Decimal('0.00'))

            # Create Stock Batch
            StockBatch.objects.create(
                product=product,
                warehouse=warehouse,
                supplier=supplier,
                purchase_invoice=locked_invoice,
                batch_number=item.batch_number,
                expiry_date=item.expiry_date,
                purchase_price=effective_cost,
                initial_quantity=total_qty,
                current_quantity=total_qty
            )

            # Update SupplierProduct link
            from django.db.models import F
            sp_link, _ = SupplierProduct.objects.get_or_create(
                supplier=supplier, product=product
            )
            sp_link.last_purchase_price = unit_price
            if bonus_qty > 0:
                sp_link.last_bonus_details = f"بونص {bonus_qty} على كمية {qty}"
            sp_link.save()

            # Log StockTransaction
            StockTransaction.objects.create(
                product=product,
                warehouse=warehouse,
                transaction_type='IN',
                quantity=total_qty,
                unit_price=unit_price,
                cost_price=effective_cost,
                reference_number=f"شراء #{locked_invoice.id}",
                note=f"فاتورة مشتريات رقم {locked_invoice.invoice_number} من {supplier.name}",
                created_by=user
            )

            # Update WarehouseStock
            ws, _ = WarehouseStock.objects.get_or_create(product=product, warehouse=warehouse)
            from django.db.models import F as FF
            ws.quantity = FF('quantity') + total_qty
            ws.save()

            # Update weighted average cost on Product (Layer-2 policy: store may opt out
            # of auto cost recalculation on receipt and manage cost manually instead).
            old_cost = product.cost_price
            if get_policy('purchases.update_cost_on_receipt'):
                product.update_cost_price()

            # Layer 2 'purchases.update_sale_price_on_receipt' — suggest (never auto-apply)
            # a new sale price preserving this product's existing markup over cost. Computed
            # from the cost BEFORE update_cost_price() above, so the suggestion reflects the
            # markup the store actually had at the moment of receipt.
            if suggest_prices:
                suggestion = _suggest_new_sale_price(product, old_cost, effective_cost)
                if suggestion:
                    price_suggestions.append(suggestion)

        # Mark as applied
        locked_invoice.is_stock_applied = True
        locked_invoice.status = 'CONFIRMED'
        locked_invoice.save(update_fields=['is_stock_applied', 'status'])

        return True, price_suggestions


def reverse_purchase_invoice_stock(invoice, user=None):
    """
    Reverse the stock from a confirmed purchase invoice (for cancellation).
    Reduces StockBatch quantities, logs reversal transactions, and updates warehouse stock.
    """
    from products.models import (
        StockBatch, StockTransaction, WarehouseStock, PurchaseInvoice
    )

    with db_transaction.atomic():
        locked_invoice = PurchaseInvoice.objects.select_for_update().get(pk=invoice.pk)

        if not locked_invoice.is_stock_applied:
            # Already reversed / was draft
            return False

        if locked_invoice.status == 'CANCELLED':
            return False

        warehouse = locked_invoice.warehouse

        for item in locked_invoice.items.all():
            product = item.product
            total_qty = item.quantity + item.bonus_quantity

            # Find batches linked to this invoice
            linked_batches = (
                StockBatch.objects
                .select_for_update()
                .filter(product=product, warehouse=warehouse, purchase_invoice=locked_invoice)
            )

            for batch in linked_batches:
                # Deduct the restored quantity from the batch
                # Cannot go below 0
                actual_deduct = min(batch.current_quantity, batch.initial_quantity)
                batch.current_quantity = max(Decimal('0.00'), batch.current_quantity - actual_deduct)
                batch.save()

            # Update WarehouseStock
            ws = WarehouseStock.objects.filter(product=product, warehouse=warehouse).first()
            if ws:
                ws.quantity = max(Decimal('0.00'), ws.quantity - total_qty)
                ws.save()

            # Log reversal transaction
            StockTransaction.objects.create(
                product=product,
                warehouse=warehouse,
                transaction_type='RET_OUT',
                quantity=total_qty,
                unit_price=item.unit_price,
                cost_price=item.unit_price,
                reference_number=f"إلغاء #{locked_invoice.id}",
                note=f"إلغاء فاتورة مشتريات رقم {locked_invoice.invoice_number}",
                created_by=user
            )

            # Update weighted average cost
            product.update_cost_price()

        locked_invoice.is_stock_applied = False
        locked_invoice.status = 'CANCELLED'
        locked_invoice.save(update_fields=['is_stock_applied', 'status'])

        return True
