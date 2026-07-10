"""Backfill receipt-voucher numbers (RV-YYYY-NNNNN) for existing customer receipts."""
from django.db import migrations


def backfill(apps, schema_editor):
    CustomerPayment = apps.get_model('crm', 'CustomerPayment')
    DocumentSequence = apps.get_model('sales', 'DocumentSequence')

    per_year = {}
    qs = (CustomerPayment.objects
          .filter(transaction_type='payment', voucher_number__isnull=True)
          .order_by('id'))
    for p in qs.iterator():
        year = p.created_at.year if p.created_at else 2025
        n = per_year.get(year, 0) + 1
        per_year[year] = n
        p.voucher_number = f"RV-{year}-{n:05d}"
        p.save(update_fields=['voucher_number'])

    for year, last in per_year.items():
        seq, _ = DocumentSequence.objects.get_or_create(
            doc_type='RV', year=year, defaults={'last_number': 0})
        if seq.last_number < last:
            seq.last_number = last
            seq.save(update_fields=['last_number'])
    if per_year:
        print(f"  Backfilled {sum(per_year.values())} receipt vouchers.")


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('crm', '0008_customerpayment_voucher_number'),
        ('sales', '0024_backfill_invoice_numbers'),
    ]
    operations = [migrations.RunPython(backfill, noop)]
