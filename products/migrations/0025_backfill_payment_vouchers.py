"""Backfill payment-voucher numbers (PV-YYYY-NNNNN) for existing supplier payments."""
from django.db import migrations


def backfill(apps, schema_editor):
    SupplierPayment = apps.get_model('products', 'SupplierPayment')
    DocumentSequence = apps.get_model('sales', 'DocumentSequence')

    per_year = {}
    qs = SupplierPayment.objects.filter(voucher_number__isnull=True).order_by('id')
    for p in qs.iterator():
        year = p.date.year if p.date else 2025
        n = per_year.get(year, 0) + 1
        per_year[year] = n
        p.voucher_number = f"PV-{year}-{n:05d}"
        p.save(update_fields=['voucher_number'])

    for year, last in per_year.items():
        seq, _ = DocumentSequence.objects.get_or_create(
            doc_type='PV', year=year, defaults={'last_number': 0})
        if seq.last_number < last:
            seq.last_number = last
            seq.save(update_fields=['last_number'])
    if per_year:
        print(f"  Backfilled {sum(per_year.values())} payment vouchers.")


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('products', '0024_supplierpayment_voucher_number'),
        ('sales', '0024_backfill_invoice_numbers'),
    ]
    operations = [migrations.RunPython(backfill, noop)]
