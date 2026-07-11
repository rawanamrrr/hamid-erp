"""
Double-entry posting engine (Phase 4.2).

Every business event emits exactly ONE balanced JournalEntry (Σ debit == Σ credit)
made of JournalLines against the nominal chart of accounts. Financial statements
(trial balance, P&L, balance sheet) are derived purely from these lines — they are the
accounting source of truth, independent of the operational `Account.balance` cache that
the POS drawer uses.

Design rules
------------
* Posting is **idempotent per source document**: a stable `reference_number` means a
  document is never double-posted; re-posting first reverses the old entry.
* Posting **never crashes the caller**. The callers wrap it, and `post_*` helpers also
  guard themselves — a journal failure must not block a sale.
* Cash/bank legs use the operational accounts (by `account_type`); nominal legs
  (revenue, COGS, inventory, AR, AP …) use the coded chart of accounts, auto-created
  on first use so a fresh install works out of the box.
"""
from decimal import Decimal

from django.db import transaction as db_transaction

from .models import Account, JournalEntry, JournalLine

ZERO = Decimal('0.00')

# Coded nominal accounts: code -> (name, type). Auto-created if missing.
NOMINAL = {
    'SALES_REVENUE': ('4100', 'إيرادات المبيعات', 'REVENUE'),
    'SALES_RETURNS': ('4110', 'مردودات المبيعات', 'REVENUE'),
    'OTHER_INCOME':  ('4200', 'إيرادات أخرى', 'REVENUE'),
    'COGS':          ('5100', 'تكلفة البضاعة المباعة - COGS', 'EXPENSE'),
    'OPEX':          ('5200', 'مصروفات تشغيلية', 'EXPENSE'),
    'INVENTORY':     ('1300', 'تقييم المخزون', 'ASSET'),
    'AR':            ('1200', 'العملاء / ذمم مدينة', 'ASSET'),
    'AP':            ('2100', 'الموردين / ذمم دائنة', 'LIABILITY'),
    'OWNER_DRAWINGS':('3100', 'مسحوبات المالك', 'EQUITY'),
    'VAT_PAYABLE':   ('2200', 'ضريبة القيمة المضافة مستحقة الدفع (Output VAT)', 'LIABILITY'),
}

# Operational cash/bank accounts by payment method.
CASH_ACCOUNT_BY_METHOD = {
    'cash': 'CASH_DRAWER',
    'wallet': 'VODAFONE_CASH',
    'instapay': 'INSTAPAY',
    'visa': 'BANK',
    'bank': 'BANK',
}


def nominal(key):
    """Return (get_or_create) the coded nominal account for a logical key."""
    code, name, acc_type = NOMINAL[key]
    acc, _ = Account.objects.get_or_create(
        code=code, defaults={'name': name, 'account_type': acc_type, 'is_active': True},
    )
    return acc


def cash_account(method):
    """Operational cash/bank account for a payment method (by account_type)."""
    acc_type = CASH_ACCOUNT_BY_METHOD.get((method or 'cash').lower(), 'CASH_DRAWER')
    acc = Account.objects.filter(account_type=acc_type, is_active=True).first()
    if not acc:
        acc = Account.objects.create(name=acc_type, account_type=acc_type, is_active=True)
    return acc


def post_journal(reference_number, description, lines, created_by=None, posted_at=None):
    """Create one balanced JournalEntry from `lines` = [(account, debit, credit), ...].

    Re-posting the same reference first deletes the prior entry (idempotent).
    Raises ValueError if the entry is unbalanced or empty. Returns the JournalEntry.
    """
    norm = []
    total_debit = ZERO
    total_credit = ZERO
    for account, debit, credit in lines:
        debit = Decimal(str(debit or 0))
        credit = Decimal(str(credit or 0))
        if debit == ZERO and credit == ZERO:
            continue
        norm.append((account, debit, credit))
        total_debit += debit
        total_credit += credit

    if not norm:
        raise ValueError("Journal entry has no non-zero lines.")
    if (total_debit - total_credit).copy_abs() > Decimal('0.01'):
        raise ValueError(f"Unbalanced journal entry {reference_number}: "
                         f"debit {total_debit} != credit {total_credit}")

    with db_transaction.atomic():
        JournalEntry.objects.filter(reference_number=reference_number).delete()
        entry = JournalEntry.objects.create(
            reference_number=reference_number,
            description=description[:500],
            status='POSTED',
            created_by=created_by,
            **({'posted_at': posted_at} if posted_at else {}),
        )
        JournalLine.objects.bulk_create([
            JournalLine(entry=entry, account=acc, debit=d, credit=c) for acc, d, c in norm
        ])
    return entry


def unpost(reference_number):
    """Remove a document's journal entry (used when voiding)."""
    JournalEntry.objects.filter(reference_number=reference_number).delete()


# ──────────────────────────────────────────────
#  DOCUMENT-LEVEL POSTING
# ──────────────────────────────────────────────

def post_sale(order):
    """Post a sales invoice as a single balanced entry:

        Dr Cash/Wallet/Instapay   (amounts actually received)
        Dr Accounts Receivable    (total − received  → credit/debt portion)
        Dr COGS                   (Σ line cost)
            Cr Sales Revenue      (invoice total − VAT)
            Cr VAT Payable        (VAT portion, Output VAT — never counted as revenue)
            Cr Inventory          (Σ line cost)

    VAT is money collected on the tax authority's behalf, not company income — it must
    never land in Sales Revenue regardless of whether the store's prices are configured
    as VAT-inclusive or -exclusive (settings.policies 'tax.vat_included_in_price'):
    Order.vat_breakdown()'s `tax` figure is the real VAT portion of total_amount either
    way (an "included" price still contains a VAT portion, just not added on top), so
    that's what's split out here — not the raw `vat_amount` field, which is only ever
    the amount actually ADDED on top and is 0 in "included" mode.
    """
    try:
        total = Decimal(str(order.total_amount or 0))
        if total <= 0 and not order.items.exists():
            return None

        lines = []
        # Received cash legs. Prefer the split fields; fall back to received_amount as a
        # single leg (legacy orders that set received_amount without the split).
        split = [
            (Decimal(str(order.cash_paid or 0)), 'cash'),
            (Decimal(str(order.wallet_paid or 0)), 'wallet'),
            (Decimal(str(order.instapay_paid or 0)), 'instapay'),
            (Decimal(str(order.visa_paid or 0)), 'visa'),
        ]
        split_sum = sum((a for a, _ in split), ZERO)
        received = Decimal(str(order.received_amount or 0))

        if split_sum > 0:
            for amt, method in split:
                if amt > 0:
                    lines.append((cash_account(method), amt, ZERO))
            received_for_ar = split_sum
        elif received > 0:
            lines.append((cash_account(str(order.payment_method or 'cash')), received, ZERO))
            received_for_ar = received
        else:
            received_for_ar = ZERO

        # AR leg: positive => customer owes (debit); negative => overpaid/credit (credit AR).
        ar_amount = total - received_for_ar
        if ar_amount > 0:
            lines.append((nominal('AR'), ar_amount, ZERO))
        elif ar_amount < 0:
            lines.append((nominal('AR'), ZERO, -ar_amount))

        # Revenue (credit) = invoice total minus the VAT portion — VAT is collected on
        # the tax authority's behalf and posted to its own liability account instead.
        vat = order.vat_breakdown()
        vat_amount = Decimal(str(vat['tax'])) if vat else ZERO
        lines.append((nominal('SALES_REVENUE'), ZERO, total - vat_amount))
        if vat_amount > 0:
            lines.append((nominal('VAT_PAYABLE'), ZERO, vat_amount))

        # COGS / Inventory legs
        cogs_total = sum((it.cogs for it in order.items.all()), ZERO)
        cogs_total = Decimal(str(cogs_total or 0))
        if cogs_total > 0:
            lines.append((nominal('COGS'), cogs_total, ZERO))
            lines.append((nominal('INVENTORY'), ZERO, cogs_total))

        return post_journal(
            reference_number=f"SALE-{order.id}",
            description=f"فاتورة مبيعات #{order.id}",
            lines=lines,
            created_by=order.user,
            posted_at=order.created_at,
        )
    except Exception:
        import logging
        logging.getLogger(__name__).exception("post_sale failed for order %s", getattr(order, 'id', '?'))
        return None


def post_refund(return_invoice):
    """Post a sales return:

        Dr Sales Returns          (refund total − VAT portion)
        Dr VAT Payable            (VAT portion — reverses the liability the original sale
                                    posted; the refunded VAT was never company revenue, so
                                    it must not land in Sales Returns either)
            Cr Cash  (if cash refund)  OR  Cr AR (if credited to customer)
        Dr Inventory              (Σ returned cost)
            Cr COGS               (Σ returned cost)
    """
    try:
        total = Decimal(str(return_invoice.total_refund_amount or 0))
        lines = []
        if total > 0:
            vat_amount = Decimal(str(return_invoice.vat_amount or 0))
            lines.append((nominal('SALES_RETURNS'), total - vat_amount, ZERO))
            if vat_amount > 0:
                lines.append((nominal('VAT_PAYABLE'), vat_amount, ZERO))
            if return_invoice.refund_method == 'customer_credit':
                lines.append((nominal('AR'), ZERO, total))
            else:
                lines.append((cash_account('cash'), ZERO, total))

        # Goods coming back into inventory at their original cost
        cost_total = ZERO
        for ri in return_invoice.items.all():
            unit_cost = ZERO
            if return_invoice.original_order_id and ri.product_id:
                oi = return_invoice.original_order.items.filter(product_id=ri.product_id).first()
                if oi:
                    unit_cost = oi.cost_price
            elif ri.product_id and ri.product:
                unit_cost = ri.product.cost_price or ZERO
            cost_total += Decimal(str(unit_cost or 0)) * Decimal(str(ri.quantity or 0))
        if cost_total > 0:
            lines.append((nominal('INVENTORY'), cost_total, ZERO))
            lines.append((nominal('COGS'), ZERO, cost_total))

        if not lines:
            return None
        return post_journal(
            reference_number=f"RET-{return_invoice.id}",
            description=f"مرتجع مبيعات #{return_invoice.id}",
            lines=lines,
            created_by=return_invoice.user,
            posted_at=return_invoice.created_at,
        )
    except Exception:
        import logging
        logging.getLogger(__name__).exception("post_refund failed for return %s", getattr(return_invoice, 'id', '?'))
        return None


# Map standalone-Transaction types to their nominal contra account (the cash/bank
# side is the Transaction's own account). debit_nominal=True means the nominal account
# is debited and the cash account credited (money leaving), else the reverse.
_TXN_RULES = {
    'EXPENSE':          ('OPEX', True),
    'WITHDRAWAL':       ('OWNER_DRAWINGS', True),
    'SUPPLIER_PAYMENT': ('AP', True),
    'INCOME':           ('OTHER_INCOME', False),
}


def post_cash_transaction(txn):
    """Post a standalone cash-movement Transaction (expense/income/withdrawal/supplier
    payment/transfer). SALE/REFUND are posted at document level and skipped here."""
    try:
        if txn.transaction_type in ('SALE', 'REFUND'):
            return None

        cash = txn.account
        amount = Decimal(str(txn.amount or 0))
        if amount <= 0:
            return None

        if txn.transaction_type == 'TRANSFER' and txn.to_account_id:
            lines = [(txn.to_account, amount, ZERO), (cash, ZERO, amount)]
        elif txn.transaction_type == 'DEPOSIT':
            # Cash into safe/bank from drawer — treat as a transfer when possible.
            if txn.to_account_id:
                lines = [(txn.to_account, amount, ZERO), (cash, ZERO, amount)]
            else:
                # No to_account: both current callers (CashCustody.settle() and
                # collect_tailoring_payment) are cash collected LATER against an order
                # that was already posted unpaid by post_sale() — its unpaid portion sat
                # in AR from day one. Without this, that receivable would never clear
                # even once the cash physically lands in the drawer (a real, previously
                # unposted gap — see driver_return_settle's docstring).
                lines = [(cash, amount, ZERO), (nominal('AR'), ZERO, amount)]
        else:
            rule = _TXN_RULES.get(txn.transaction_type)
            if not rule:
                return None
            nom_key, debit_nominal = rule
            nom = nominal(nom_key)
            if debit_nominal:
                lines = [(nom, amount, ZERO), (cash, ZERO, amount)]
            else:
                lines = [(cash, amount, ZERO), (nom, ZERO, amount)]

        return post_journal(
            reference_number=f"TXN-{txn.id}",
            description=txn.description or txn.get_transaction_type_display(),
            lines=lines,
            created_by=txn.created_by,
            posted_at=txn.created_at,
        )
    except Exception:
        import logging
        logging.getLogger(__name__).exception("post_cash_transaction failed for txn %s", getattr(txn, 'id', '?'))
        return None
