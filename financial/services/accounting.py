"""
DEPRECATED — replaced by financial/posting.py (Phase 4.2).

The previous auto-journal stub posted both legs of every transaction to the cash
drawer, producing meaningless (though technically balanced) entries. The real
double-entry posting engine now lives in `financial.posting`:

    post_sale(order), post_refund(return_invoice), post_cash_transaction(txn)

This module is intentionally left empty to avoid accidental imports of the old logic.
"""
