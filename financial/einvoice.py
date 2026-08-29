"""
E-invoice document builder (Phase 10.2) — Egypt ETA readiness.

Produces a JSON document for a sales order in a structure close to the Egyptian Tax
Authority (ETA) e-invoice schema: issuer (shop tax id), receiver (customer tax id),
invoice lines with item codes (EGS/GS1), quantities, unit prices and tax. This is an
export/readiness layer — submission to ETA is a pluggable adapter for later.
"""
from decimal import Decimal


def _money(v):
    return float(Decimal(str(v or 0)).quantize(Decimal('0.01')))


def build_invoice_json(order):
    from settings.models import SystemSetting
    from settings.policies import get_policy
    s = SystemSetting.objects.first()
    vat_rate = Decimal(str(get_policy('tax.vat_rate') or 0))
    factor = (vat_rate / (Decimal('100') + vat_rate)) if vat_rate > 0 else Decimal('0')

    lines = []
    for it in order.items.select_related('product').all():
        product = it.product
        code = ''
        code_type = 'EGS'
        if product:
            if product.egs_code:
                code = product.egs_code
            elif product.barcode:
                code = product.barcode
                code_type = 'GS1'
            else:
                code = product.sku
                code_type = 'SKU'
        gross = Decimal(str(it.price or 0)) * Decimal(str(it.quantity or 0))
        tax = (gross * factor).quantize(Decimal('0.01'))
        lines.append({
            'description': product.name if product else '',
            'itemCode': code,
            'itemCodeType': code_type,
            'quantity': float(it.quantity or 0),
            'unitPrice': _money(it.price),
            'salesTotal': _money(gross),
            'taxableAmount': _money(gross - tax),
            'taxAmount': _money(tax),
        })

    total = Decimal(str(order.total_amount or 0))
    total_tax = (total * factor).quantize(Decimal('0.01'))

    cust = order.customer
    return {
        'documentType': 'I',  # Invoice
        # accounting_number, not display_number: the receipt number people see
        # restarts at 1 every shift, and an e-invoice id has to stay unique.
        'internalId': order.accounting_number,
        'dateTimeIssued': order.created_at.isoformat() if order.created_at else None,
        'issuer': {
            'name': s.shop_name if s else '',
            'taxId': get_policy('tax.vat_number') or '',
            'address': (s.address if s else '') or '',
        },
        'receiver': {
            'name': cust.display_customer if (cust and hasattr(cust, 'display_customer')) else (
                f"{cust.first_name} {cust.last_name}" if cust else 'عميل نقدي'),
            'taxId': (cust.tax_number if cust else '') or '',
        },
        'invoiceLines': lines,
        'totalSalesAmount': _money(total),
        'totalTaxAmount': _money(total_tax),
        'netAmount': _money(total - total_tax),
        'vatRate': float(vat_rate),
    }
