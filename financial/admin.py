from django.contrib import admin
from .models import Account, DailyShift, Transaction, ShiftEmailLog, JournalEntry, JournalLine, EmployeeSalary, DealDiscount

class JournalLineInline(admin.TabularInline):
    model = JournalLine
    extra = 2

@admin.register(JournalEntry)
class JournalEntryAdmin(admin.ModelAdmin):
    list_display = ('reference_number', 'description', 'posted_at', 'status', 'created_by')
    list_filter = ('status', 'posted_at')
    search_fields = ('reference_number', 'description', 'created_by__username')
    inlines = [JournalLineInline]

@admin.register(JournalLine)
class JournalLineAdmin(admin.ModelAdmin):
    list_display = ('id', 'entry', 'account', 'debit', 'credit')
    list_filter = ('account',)
    search_fields = ('entry__reference_number', 'account__name')

@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ('name', 'account_type', 'balance', 'is_active')
    list_filter = ('account_type', 'is_active')
    search_fields = ('name',)

@admin.register(DailyShift)
class DailyShiftAdmin(admin.ModelAdmin):
    list_display = ('id', 'employee', 'start_time', 'end_time', 'is_closed', 'difference')
    list_filter = ('is_closed', 'start_time')
    search_fields = ('employee__username',)

@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ('id', 'transaction_type', 'amount', 'account', 'created_by', 'created_at', 'journal_entry')
    list_filter = ('transaction_type', 'created_at', 'account')
    search_fields = ('description', 'created_by__username')


@admin.register(ShiftEmailLog)
class ShiftEmailLogAdmin(admin.ModelAdmin):
    list_display = ('id', 'shift', 'sent_at', 'success')
    list_filter = ('success', 'sent_at')
    search_fields = ('shift__id', 'error_message')

@admin.register(EmployeeSalary)
class EmployeeSalaryAdmin(admin.ModelAdmin):
    list_display = ('employee', 'basic_salary', 'allowances', 'deductions', 'net_salary', 'updated_at')
    search_fields = ('employee__username',)

@admin.register(DealDiscount)
class DealDiscountAdmin(admin.ModelAdmin):
    list_display = ('name', 'discount_type', 'value', 'minimum_order_value', 'start_date', 'end_date', 'coupon_code', 'is_active')
    list_filter = ('is_active', 'discount_type')
    search_fields = ('name', 'coupon_code')
