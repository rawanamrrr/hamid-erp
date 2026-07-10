from financial.models import Account, Transaction, DailyShift
from django.utils import timezone
from decimal import Decimal
import logging

logger = logging.getLogger(__name__)

def get_active_shift():
    """
    Returns the currently open shift, or None if no shift is open.
    Does NOT auto-create a shift.
    """
    return DailyShift.objects.filter(is_closed=False).last()


def get_or_create_active_shift(user):
    """
    Returns the currently open shift.
    If no shift is open, automatically opens a new one for `user`.
    This is called on every POS transaction so that cashiers don't need
    to manually open a shift before making a sale.
    """
    shift = DailyShift.objects.filter(is_closed=False).last()
    if shift:
        return shift, False  # (shift, created)

    # Auto-open a new shift
    shift = DailyShift.objects.create(
        employee=user,
        start_balance=0,
    )
    logger.info(f"Auto-opened shift #{shift.id} for user '{user.username}' on POS transaction.")
    return shift, True  # (shift, created)

def record_sale_transaction(order, user):
    """
    Creates financial transactions for a newly created order.
    Handles split payments (Cash, Wallet, InstaPay) by creating separate transactions.
    Auto-opens a shift if none is currently active.
    """
    try:
        # Get active shift — auto-create one if none is open
        current_shift, shift_created = get_or_create_active_shift(user)
        if shift_created:
            logger.info(f"Shift #{current_shift.id} was auto-created for Order #{order.id}")

        # Define payment components to check
        # (Amount, Account Type, Default Name)
        # NOTE (Phase 1.6): credit_paid is intentionally EXCLUDED. Paying from the
        # customer's existing credit balance is NOT new money — it must not hit a cash
        # account nor create revenue. The customer ledger handles it automatically
        # because received_amount already excludes credit_paid (order debt = total -
        # received naturally consumes the prior credit).
        payments = [
            (order.cash_paid, 'CASH_DRAWER', 'درج الكاشير'),
            (order.wallet_paid, 'VODAFONE_CASH', 'محفظة فودافون كاش'),
            (order.instapay_paid, 'INSTAPAY', 'إنستا باي'),
            (order.visa_paid, 'BANK', 'حساب البنك'),
        ]

        # Fallback: If split payment fields are 0 (Legacy or direct payment where only received_amount is set)
        total_split = sum([p[0] for p in payments if p[0]])
        
        # FIX: We only trigger fallback if split payments are 0 AND there is actually a received amount.
        # Previously, this used 'order.total_amount', which caused unpaid invoices (cost 300, paid 0)
        # to be recorded as fully paid transactions.
        if total_split == 0 and order.received_amount > 0:
            pm = str(order.payment_method).lower()
            amount_to_record = order.received_amount # USE THIS, NOT TOTAL_AMOUNT

            if 'visa' in pm or 'bank' in pm:
                payments = [(amount_to_record, 'BANK', 'حساب البنك')]
            elif 'vodafone' in pm or 'wallet' in pm:
                payments = [(amount_to_record, 'VODAFONE_CASH', 'محفظة فودافون كاش')]
            elif 'insta' in pm:
                payments = [(amount_to_record, 'INSTAPAY', 'إنستا باي')]
            else:
                payments = [(amount_to_record, 'CASH_DRAWER', 'درج الكاشير')]

        # Process each payment component
        for amount, acc_type, acc_name in payments:
            if amount and amount > 0:
                # 1. Find Account safely
                account = Account.objects.filter(account_type=acc_type).first()
                
                # If not found, create it
                if not account:
                    account = Account.objects.create(
                        name=acc_name,
                        account_type=acc_type,
                        balance=0.00,
                        is_active=True
                    )

                # 2. Create Transaction (Logic in models.py will update Account balance via F expression)
                Transaction.objects.create(
                    shift=current_shift,
                    account=account,
                    transaction_type='SALE',
                    amount=amount,
                    description=f"مبيعات أوردر رقم #{order.id}",
                    created_by=user,
                    order=order,  # Phase 1.2: link to source document
                )
                logger.info("SUCCESS: Recorded %s to %s (%s)", amount, account.name, acc_type)

        # Phase 4.2: post the full double-entry for this invoice (revenue + AR + COGS
        # + inventory). Idempotent per order; self-guarded so it can't break the sale.
        from financial.posting import post_sale
        post_sale(order)

    except Exception as e:
        # Phase 1.12: DO NOT swallow. This runs inside the order's atomic block; a
        # silent failure here would commit a sale with no financial record (money
        # vanishes from the books). Re-raise so the whole order rolls back and the
        # cashier sees an error instead of a phantom paid invoice.
        logger.error("CRITICAL ERROR recording financial transaction for Order %s: %s", order.id, e)
        raise
def reverse_order_financials(order, user, reason=""):
    """
    Reverses all financial transactions linked to an order.
    Creates new transactions with type REFUND to balance the books.
    """
    try:
        from financial.models import Account, Transaction
        current_shift = get_active_shift()
        
        reversals = [
            (order.cash_paid, 'CASH_DRAWER', 'درج الكاشير'),
            (order.wallet_paid, 'VODAFONE_CASH', 'محفظة فودافون كاش'),
            (order.instapay_paid, 'INSTAPAY', 'إنستا باي'),
            (order.visa_paid, 'BANK', 'حساب البنك'),
        ]
        
        # Fallback for reversals if split fields are 0
        total_reversal = sum([r[0] for r in reversals if r[0]])
        if total_reversal == 0 and order.received_amount > 0:
            pm = str(order.payment_method).lower()
            amount_to_reverse = order.received_amount
            if 'visa' in pm or 'bank' in pm:
                reversals = [(amount_to_reverse, 'BANK', 'حساب البنك')]
            elif 'vodafone' in pm or 'wallet' in pm:
                reversals = [(amount_to_reverse, 'VODAFONE_CASH', 'محفظة فودافون كاش')]
            elif 'insta' in pm:
                reversals = [(amount_to_reverse, 'INSTAPAY', 'إنستا باي')]
            else:
                reversals = [(amount_to_reverse, 'CASH_DRAWER', 'درج الكاشير')]

        for amount, acc_type, acc_name in reversals:
            if amount and amount > 0:
                account = Account.objects.filter(account_type=acc_type).first()
                if not account:
                     account = Account.objects.create(name=acc_name, account_type=acc_type, balance=0, is_active=True)
                
                Transaction.objects.create(
                    shift=current_shift,
                    account=account,
                    transaction_type='REFUND',
                    amount=amount,
                    description=f"إلغاء أوردر رقم #{order.id} - السبب: {reason}",
                    created_by=user,
                    order=order,  # Phase 1.2: link to source document
                )
        return True
    except Exception as e:
        # Mirror record_sale_transaction's rule (see its comment above): DO NOT swallow.
        # This runs inside delete_order_ajax's atomic block; a silent failure here would
        # still mark the order VOID with stock already restored, while the cash/wallet/
        # instapay/visa REFUND transactions silently never get created — the drawer's
        # recorded balance stays overstated with no error shown to the cashier. Re-raise
        # so the whole void rolls back instead.
        logger.error("CRITICAL ERROR reversing financials for Order %s: %s", order.id, e)
        raise
