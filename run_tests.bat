@echo off
REM Phase 0.4 invariant test harness.
REM Builds the schema directly from models (test_settings) to bypass the
REM legacy migration-history duplication, and forces UTF-8 so Arabic
REM post_migrate prints don't crash the Windows console.
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
python manage.py test products.test_inventory sales.test_money sales.test_services financial.test_posting crm.test_statements financial.test_period_lock sales.test_numbering crm.test_vouchers financial.test_daily_summary financial.test_shift_report products.test_low_stock sales.test_returns_doc crm.test_aging products.test_movement accounts.test_op_limits crm.test_allocation financial.test_reports_batch financial.test_vat financial.test_analytics crm.test_customer_data products.test_stocktake crm.test_credit_limit products.test_landed_cost products.test_price_comparison products.test_pricing sales.test_quotation financial.test_einvoice products.test_insights crm.test_whatsapp financial.test_position sales.test_visibility --settings=textile_pos.test_settings %*
