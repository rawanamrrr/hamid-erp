from django.urls import path
from . import views

app_name = 'financial'

urlpatterns = [
    # Dashboard
    path('', views.financial_dashboard, name='dashboard'),

    # Financial statements (Phase 4.6 — derived from the general journal)
    path('reports/trial-balance/', views.trial_balance_view, name='trial_balance'),
    path('reports/income-statement/', views.income_statement_view, name='income_statement'),
    path('period-lock/', views.period_lock_view, name='period_lock'),
    path('reports/daily-summary/', views.daily_summary_view, name='daily_summary'),
    path('reports/profitability/', views.profitability_view, name='profitability'),
    path('reports/vat/', views.vat_report_view, name='vat_report'),
    path('reports/analytics/', views.sales_analytics_view, name='sales_analytics'),
    path('reports/position/', views.financial_position_view, name='financial_position'),
    path('system-health/', views.system_health, name='system_health'),
    path('shift/x-report/', views.shift_x_report, name='shift_x_report'),

    # Transactions
    path('transactions/', views.all_transactions, name='all_transactions'),
    path('transactions/print/', views.print_all_transactions, name='print_all_transactions'),
    path('transaction/add/', views.add_transaction, name='add_transaction'),

    # Withdrawal (سحب) — dedicated page
    path('withdrawal/', views.withdrawal_view, name='withdrawal'),

    # Shift Management
    path('history/', views.shift_history, name='shift_history'),
    path('shift/start/', views.start_shift, name='start_shift'),
    path('shift/start-ajax/', views.start_shift_ajax, name='start_shift_ajax'),
    path('shift/close/<int:pk>/', views.close_shift, name='close_shift'),
    path('shift/print/<int:pk>/', views.print_shift_summary, name='print_shift_summary'),

    # Account Management
    path('accounts/', views.account_list, name='account_list'),
    path('accounts/create/', views.account_create, name='account_create'),
    path('accounts/<int:pk>/edit/', views.account_edit, name='account_edit'),
    path('accounts/<int:pk>/', views.account_detail, name='account_detail'),

    # Salaries & Payroll (Normal User Pages)
    path('salaries/attendance/', views.attendance_daily, name='attendance_daily'),
    path('salaries/attendance-settings/', views.attendance_settings, name='attendance_settings'),
    path('salaries/', views.salary_list, name='salary_list'),
    path('salaries/create/', views.salary_create, name='salary_create'),
    path('salaries/<int:pk>/edit/', views.salary_edit, name='salary_edit'),
    path('salaries/<int:pk>/pay/', views.salary_pay, name='salary_pay'),
    # Payroll register: payslips & advances
    path('salaries/<int:salary_pk>/payslip/', views.payslip_generate, name='payslip_generate'),
    path('payslips/<int:pk>/', views.payslip_detail, name='payslip_detail'),
    path('payslips/<int:pk>/pay/', views.payslip_pay, name='payslip_pay'),
    path('payslips/<int:pk>/print/', views.payslip_print, name='payslip_print'),
    path('advances/', views.advance_list, name='advance_list'),
    path('advances/create/', views.advance_create, name='advance_create'),
    path('advances/<int:pk>/edit/', views.advance_edit, name='advance_edit'),

    # Deals & Discounts (Normal User Pages)
    path('deals/', views.deal_list, name='deal_list'),
    path('deals/report/', views.deal_report, name='deal_report'),
    path('deals/create/', views.deal_create, name='deal_create'),
    path('deals/<int:pk>/edit/', views.deal_edit, name='deal_edit'),
    path('deals/<int:pk>/delete/', views.deal_delete, name='deal_delete'),
]