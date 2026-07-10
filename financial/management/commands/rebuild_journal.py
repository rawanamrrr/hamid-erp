"""
rebuild_journal — wipe and re-post the entire general journal from source documents.

Use after deploying the Phase 4.2 posting engine (the old auto-stub left meaningless
entries), or any time you suspect the journal has drifted. Posting is idempotent, so
this is safe to re-run.

    python manage.py rebuild_journal
"""
from django.core.management.base import BaseCommand
from django.db import transaction as db_transaction

from financial.models import JournalEntry
from financial.posting import post_sale, post_refund, post_cash_transaction


class Command(BaseCommand):
    help = "Delete all journal entries and re-post them from orders, returns and transactions."

    def handle(self, *args, **opts):
        from sales.models import Order, ReturnInvoice
        from financial.models import Transaction

        with db_transaction.atomic():
            deleted = JournalEntry.objects.count()
            JournalEntry.objects.all().delete()
            self.stdout.write(f"Cleared {deleted} existing journal entries.")

            sales = 0
            for order in Order.objects.exclude(status='void').iterator():
                if post_sale(order):
                    sales += 1
            self.stdout.write(self.style.SUCCESS(f"Posted {sales} sales invoices."))

            rets = 0
            for ri in ReturnInvoice.objects.iterator():
                if post_refund(ri):
                    rets += 1
            self.stdout.write(self.style.SUCCESS(f"Posted {rets} return invoices."))

            txns = 0
            for txn in Transaction.objects.exclude(transaction_type__in=['SALE', 'REFUND']).iterator():
                if post_cash_transaction(txn):
                    txns += 1
            self.stdout.write(self.style.SUCCESS(f"Posted {txns} standalone transactions."))

        # Report whether the rebuilt journal balances.
        from django.db.models import Sum
        from financial.models import JournalLine
        agg = JournalLine.objects.aggregate(d=Sum('debit'), c=Sum('credit'))
        d = agg['d'] or 0
        c = agg['c'] or 0
        status = self.style.SUCCESS("BALANCED") if abs(d - c) < 0.01 else self.style.ERROR("UNBALANCED")
        self.stdout.write(f"Journal totals — debit {d}, credit {c} -> {status}")
