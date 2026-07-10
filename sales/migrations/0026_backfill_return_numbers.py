"""Backfill return-document numbers (RET-YYYY-NNNNN) for existing returns."""
from django.db import migrations


def backfill(apps, schema_editor):
    ReturnInvoice = apps.get_model('sales', 'ReturnInvoice')
    DocumentSequence = apps.get_model('sales', 'DocumentSequence')

    per_year = {}
    qs = ReturnInvoice.objects.filter(return_number__isnull=True).order_by('id')
    for ri in qs.iterator():
        year = ri.created_at.year if ri.created_at else 2025
        n = per_year.get(year, 0) + 1
        per_year[year] = n
        ri.return_number = f"RET-{year}-{n:05d}"
        ri.save(update_fields=['return_number'])

    for year, last in per_year.items():
        seq, _ = DocumentSequence.objects.get_or_create(
            doc_type='RET', year=year, defaults={'last_number': 0})
        if seq.last_number < last:
            seq.last_number = last
            seq.save(update_fields=['last_number'])
    if per_year:
        print(f"  Backfilled {sum(per_year.values())} return numbers.")


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('sales', '0025_returninvoice_reason_category_and_more'),
    ]
    operations = [migrations.RunPython(backfill, noop)]
